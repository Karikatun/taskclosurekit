"""Small v2 adapter: fixed trusted argv, bounded execution and discarded output."""
import math
import os
import selectors
import signal
import subprocess
import time
from taskproof import runner, snapshot, core
from .presets import REGISTRY

FILE_WRITE_LIMIT = snapshot.FILE_LIMIT


def resource_profile(preset):
    return {**runner.resource_profile(), "cpu_seconds": math.ceil(preset.timeout),
            "file_bytes": FILE_WRITE_LIMIT if preset.permitted_writes else 0}


def bounded_process(preset, repository):
    if not runner.supported():
        raise RuntimeError("unsupported_resource_limits")
    profile = resource_profile(preset)
    def limits():
        import resource
        for key, value in ((resource.RLIMIT_CPU, profile["cpu_seconds"]),
                           (resource.RLIMIT_FSIZE, profile["file_bytes"]),
                           (resource.RLIMIT_NOFILE, profile["open_files"]),
                           (resource.RLIMIT_CORE, profile["core_bytes"])):
            resource.setrlimit(key, (value, value))
        if profile["address_space_bytes"] is not None:
            resource.setrlimit(resource.RLIMIT_AS, (profile["address_space_bytes"], profile["address_space_bytes"]))
    started = time.monotonic()
    process = subprocess.Popen([preset.executable, *preset.argv], cwd=repository,
        env=dict(preset.environment_allowlist), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, preexec_fn=limits)
    selector = selectors.DefaultSelector()
    counts = {"stdout": 0, "stderr": 0}
    timeout_hit = truncated = False
    def kill():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        for name in counts:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = preset.timeout - (time.monotonic() - started)
            if remaining <= 0:
                timeout_hit = True
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                counts[key.data] += len(chunk)
                if sum(counts.values()) > preset.output_limit:
                    truncated = True
                    break
            if truncated:
                break
        if timeout_hit or truncated:
            kill()
        try:
            process.wait(timeout=max(0.01, preset.timeout - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            timeout_hit = True
            kill()
            process.wait()
    except BaseException:
        kill()
        process.wait()
        raise
    finally:
        # A successful leader can leave descendants with DEVNULL streams. Stop
        # the owned group on every exit before application post-run capture.
        kill()
        process.wait()
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return {"kind": "discarded-output-v1", "stdout_bytes": counts["stdout"], "stderr_bytes": counts["stderr"],
            "exit_code": process.returncode, "timeout": timeout_hit, "truncated": truncated,
            "duration_ms": int((time.monotonic() - started) * 1000)}


def run(preset_id, repository, registry=REGISTRY):
    preset = registry.get(preset_id)
    if preset.id == runner.PRESET:
        if preset.argv != runner.CHECK_ARGS:
            raise RuntimeError("execution_or_contract_changed")
        return runner.run_check(snapshot.git_binary(), repository)
    snapshot.safe_path(preset.executable)
    return bounded_process(preset, repository)


def permitted_outputs(before, after, preset):
    """Checks may change only explicitly approved outputs, never inputs or controls."""
    if any(before[name] != after[name] for name in ("controls", "source_bindings", "execution", "contract_input")):
        return False
    if before.get("capabilities") != after.get("capabilities"):
        return False
    for item in set(before["entries"]) | set(after["entries"]):
        if before["entries"].get(item) == after["entries"].get(item):
            continue
        if not core.scope_contains(item, preset.permitted_writes):
            return False
    return True
