"""Small v2 adapter: fixed trusted argv, bounded execution and discarded output."""
import math
import os
import re
import selectors
import signal
import subprocess
import time
from .._primitives import runner, snapshot, policy
from .presets import REGISTRY
from ..domain.contract import fields

FILE_WRITE_LIMIT = snapshot.FILE_LIMIT


def resource_profile(preset):
    if preset.execution_policy:
        return preset.execution_policy.profile(bool(preset.permitted_writes))
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
    retained = {"stdout": bytearray(), "stderr": bytearray()}
    diagnostic_limit = preset.execution_policy.diagnostic_limit if preset.execution_policy else 0
    timeout_hit = truncated = False
    group_stopped = False
    def kill():
        nonlocal group_stopped
        if group_stopped:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        group_stopped = True
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
                available = diagnostic_limit - sum(len(value) for value in retained.values())
                if available > 0:
                    retained[key.data].extend(chunk[:available])
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
    result = {"kind": "discarded-output-v1", "stdout_bytes": counts["stdout"], "stderr_bytes": counts["stderr"],
            "exit_code": process.returncode, "timeout": timeout_hit, "truncated": truncated,
            "duration_ms": int((time.monotonic() - started) * 1000)}
    if preset.execution_policy:
        diagnostics, output_loss = diagnostic_projection(retained, counts, diagnostic_limit)
        result.update(kind="bounded-output-v2", diagnostics=diagnostics, output_loss=output_loss,
                      resource_limit=process.returncode in (-signal.SIGXCPU, -signal.SIGXFSZ))
    return result


def diagnostic_projection(retained, counts, limit):
    """Bounded conservative redaction; raw bytes never enter the result/store."""
    redacted = False
    decoded = {}
    output_loss = sum(counts.values()) > sum(len(value) for value in retained.values())
    patterns = (
        r"(?i)\b(?:authorization\s*:\s*bearer|bearer)\s+[^\s]+",
        # Quoted values may contain spaces and escaped quotes. An unterminated
        # retained quote consumes the remaining diagnostic, never a secret tail.
        r"""(?i)\b(?:password|passwd|secret|token|api[_-]?key|credential)["']?\s*[:=]\s*(?:"(?:\\.|[^"\\])*(?:"|$)|'(?:\\.|[^'\\])*(?:'|$)|[^\s,;"']+)""",
        r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)",
        r"(?i)(?:https?://)[^\s/@]+:[^\s/@]+@",
    )
    for name, raw in retained.items():
        try:
            value = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            value = bytes(raw).decode("utf-8", "replace")
            output_loss = True
        value = re.sub(r"\x1b\][^\x07]*(?:\x07|$)|\x1b\[[0-?]*[ -/]*[@-~]", "", value)
        value = "".join(character for character in value if ord(character) >= 32 or character in "\n\t\r")
        for pattern in patterns:
            value, count = re.subn(pattern, "[REDACTED]", value)
            redacted = redacted or bool(count)
        # Redaction must not grow the retained projection beyond the declared byte bound.
        decoded[name] = value.encode("utf-8")[:limit].decode("utf-8", "ignore")
    if sum(len(value.encode("utf-8")) for value in decoded.values()) > limit:
        decoded["stderr"] = decoded["stderr"].encode("utf-8")[:max(0, limit - len(decoded["stdout"].encode("utf-8")))].decode("utf-8", "ignore")
    return {"schema": "taskclosurekit/diagnostics/v1", **decoded, "redacted": redacted,
            "retention_limit": limit}, output_loss


def validate_result(result, preset):
    """Strict versioned persisted-result validator shared by producer and replay."""
    common = ("kind", "stdout_bytes", "stderr_bytes", "exit_code", "timeout", "truncated", "duration_ms")
    version_two = preset.execution_policy is not None
    fields(result, (*common, *( ("diagnostics", "output_loss", "resource_limit") if version_two else ())))
    if (result["kind"] != ("bounded-output-v2" if version_two else "discarded-output-v1") or
            any(type(result[name]) is not int for name in ("stdout_bytes", "stderr_bytes", "exit_code", "duration_ms")) or
            any(result[name] < 0 for name in ("stdout_bytes", "stderr_bytes", "duration_ms")) or
            not -64 <= result["exit_code"] <= 255 or
            any(type(result[name]) is not bool for name in ("timeout", "truncated"))):
        raise RuntimeError("invalid_check_result")
    output = result["stdout_bytes"] + result["stderr_bytes"]
    if output > preset.output_limit + 8192 or (not result["truncated"] and output > preset.output_limit):
        raise RuntimeError("invalid_check_result")
    if not version_two:
        return
    diagnostic = result["diagnostics"]
    fields(diagnostic, ("schema", "stdout", "stderr", "redacted", "retention_limit"))
    limit = preset.execution_policy.diagnostic_limit
    if (diagnostic["schema"] != "taskclosurekit/diagnostics/v1" or
            type(diagnostic["retention_limit"]) is not int or diagnostic["retention_limit"] != limit or
            type(diagnostic["redacted"]) is not bool or
            any(type(diagnostic[name]) is not str for name in ("stdout", "stderr")) or
            sum(len(diagnostic[name].encode("utf-8")) for name in ("stdout", "stderr")) > limit or
            any(type(result[name]) is not bool for name in ("output_loss", "resource_limit")) or
            output > limit and not result["output_loss"] or
            result["resource_limit"] != (result["exit_code"] in (-signal.SIGXCPU, -signal.SIGXFSZ))):
        raise RuntimeError("invalid_check_result")


def result_reason(result, preset):
    validate_result(result, preset)
    if result["timeout"]:
        return "check_timeout"
    if result["truncated"]:
        return "check_output_limit"
    if preset.execution_policy and result["resource_limit"]:
        return "check_resource_limit"
    if result["exit_code"] != 0:
        return "check_failed"
    if preset.execution_policy and result["output_loss"] and preset.execution_policy.require_complete_diagnostics:
        return "check_evidence_lost"
    return None


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
        if item.split("/")[-1].lower()==".gitattributes":
            return False
        if not policy.scope_contains(item, preset.permitted_writes):
            return False
    return True
