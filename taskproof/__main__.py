import argparse
import json
import subprocess
import sys

from . import core


def main():
    parser = argparse.ArgumentParser(prog="taskproof")
    parser.add_argument("--store", required=True)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("contract").add_argument("input")
    for name in ("baseline", "check", "close", "resume"):
        commands.add_parser(name)
    commands.add_parser("review").add_argument("input")
    args = parser.parse_args()
    try:
        if args.action == "contract":
            result = core.create(args.store, args.input)
        else:
            result = core.execute(args.store, args.action, getattr(args, "input", None))
        code = 1 if result["state"] == "BLOCKED" else 0
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, subprocess.SubprocessError):
        # Never emit exception filenames, raw commands/output, credentials or imported text.
        error = sys.exc_info()[1]
        reason = str(error) if isinstance(error, RuntimeError) else "environment_or_input_error"
        allowed = set("abcdefghijklmnopqrstuvwxyz_0123456789")
        if not reason or len(reason) > 96 or any(char not in allowed for char in reason):
            reason = "environment_or_input_error"
        result = {"state": "BLOCKED", "reason": reason}
        code = 1
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
