"""Fixed-command execution. Raw output is counted and discarded before persistence."""

import os
import selectors
import signal
import subprocess
import sys
import time


PRESET = "git-index-whitespace-v1"
CHECK_ARGS = ("--no-pager", "diff", "--cached", "--check", "--no-ext-diff",
              "--no-textconv", "HEAD", "--")
TIMEOUT = 5.0
OUTPUT_LIMIT = 65536


def environment():
    # No inherited PATH, HOME, Git variables, credentials, pager or proxy variables.
    return {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "4", "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false", "GIT_CONFIG_KEY_1": "core.hooksPath",
            "GIT_CONFIG_VALUE_1": os.devnull, "GIT_CONFIG_KEY_2": "core.pager",
            "GIT_CONFIG_VALUE_2": "cat", "GIT_CONFIG_KEY_3": "core.untrackedCache",
            "GIT_CONFIG_VALUE_3": "false"}


def resource_profile():
    return {"cpu_seconds": 5, "file_bytes": 0, "open_files": 64, "core_bytes": 0,
            "address_space_bytes": 512 * 1024 * 1024 if sys.platform.startswith("linux") else None,
            "platform": sys.platform}


def _limits():
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    memory = resource_profile()["address_space_bytes"]
    if memory is not None:
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))


def supported():
    if os.name != "posix":
        return False
    try:
        import resource
        return all(hasattr(resource, key) for key in
                   ("RLIMIT_CPU", "RLIMIT_FSIZE", "RLIMIT_NOFILE", "RLIMIT_CORE"))
    except ImportError:
        return False


def _bounded_process(argv, cwd, env, timeout=TIMEOUT, output_limit=OUTPUT_LIMIT,
                     capture=False):
    """Internal seam; production callers use fixed argv only. Never persist raw streams."""
    if not supported():
        raise RuntimeError("unsupported_resource_limits")
    if not (0 < timeout <= TIMEOUT and 0 < output_limit <= OUTPUT_LIMIT):
        raise RuntimeError("invalid_runner_limits")
    started = time.monotonic()
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, preexec_fn=_limits)
    selector = selectors.DefaultSelector()
    counts = {"stdout": 0, "stderr": 0}
    data = {"stdout": bytearray(), "stderr": bytearray()}
    timeout_hit = False
    truncated = False
    try:
        for name in counts:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                timeout_hit = True
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                counts[key.data] += len(chunk)
                if sum(counts.values()) > output_limit:
                    truncated = True
                    break
                if capture:
                    data[key.data].extend(chunk)
            if truncated:
                break
        if timeout_hit or truncated:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # Also kill an escaped descendant retaining a pipe at the deadline above.
        try:
            process.wait(timeout=max(0.01, timeout - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            timeout_hit = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    journal = {"kind": "discarded-output-v1", "stdout_bytes": counts["stdout"],
               "stderr_bytes": counts["stderr"], "exit_code": process.returncode,
               "timeout": timeout_hit, "truncated": truncated,
               "duration_ms": int((time.monotonic() - started) * 1000)}
    return journal, bytes(data["stdout"]), bytes(data["stderr"])


def run_check(git, repo):
    return _bounded_process([str(git), *CHECK_ARGS], repo, environment())[0]


def git_read(git, repo, args):
    journal, stdout, _ = _bounded_process([str(git), *args], repo, environment(), capture=True)
    if journal["exit_code"] != 0 or journal["timeout"] or journal["truncated"]:
        raise RuntimeError("git_snapshot_incomplete")
    return stdout
