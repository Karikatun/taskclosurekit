"""Isolated entry point for the bundled CLI; imports never depend on the caller cwd."""
import json
import os
from pathlib import Path
import shutil
import signal
import sys


def fail(reason):
    if "--json" in sys.argv[1:]:
        print(json.dumps({"schema": "taskclosurekit/result/v2", "operation": "unknown",
                          "operational": {"status": "environment_error"}, "task_id": None,
                          "state": "INVALID", "decision": "NOT_EVALUATED", "reasons": [reason],
                          "freshness": [], "claim": None, "next_action": "none"}, sort_keys=True))
    else:
        print("TaskClosureKit: " + reason + ". Requires Python >=3.9, POSIX resource APIs and system Git.",
              file=sys.stderr)
    return 3


def main():
    if sys.version_info < (3, 9):
        return fail("unsupported_python_version")
    if sys.platform not in ("darwin", "linux") or os.name != "posix":
        return fail("unsupported_platform")
    try:
        import resource
        if not all(hasattr(resource, key) for key in
                   ("RLIMIT_CPU", "RLIMIT_FSIZE", "RLIMIT_NOFILE", "RLIMIT_CORE")):
            return fail("unsupported_resource_limits")
    except ImportError:
        return fail("unsupported_resource_limits")
    if shutil.which("git", path="/usr/bin:/bin") is None:
        return fail("git_unavailable")
    # -I -S removes cwd, PYTHONPATH and site customization before this script runs.
    # Only the package's own parent is added; the caller's cwd is unchanged.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from taskclosurekit.cli.main import main as cli_main
    interrupted = []

    def interrupt(signum, frame):
        if not interrupted:
            interrupted.append(signum)
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    try:
        return cli_main()
    except KeyboardInterrupt:
        print("TaskClosureKit: interrupted; inspect persisted state before retrying.", file=sys.stderr)
        return 128 + interrupted[0] if interrupted else 130


if __name__ == "__main__":
    raise SystemExit(main())
