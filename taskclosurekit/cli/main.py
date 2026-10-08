import argparse
import json
import subprocess
import sys
from .. import application

class InvalidArguments(Exception):
    pass

class Parser(argparse.ArgumentParser):
    def error(self,message):
        raise InvalidArguments()

def main(argv=None):
    parser=Parser(prog="taskclosurekit")
    parser.add_argument("--store",required=True)
    parser.add_argument("--json",action="store_true")
    commands=parser.add_subparsers(dest="operation",required=True,parser_class=Parser)
    task=commands.add_parser("task"); task.add_argument("--json",action="store_true")
    task_commands=task.add_subparsers(dest="task_operation",required=True,parser_class=Parser)
    create=task_commands.add_parser("create");create.add_argument("input");create.add_argument("--preset-config");create.add_argument("--json",action="store_true")
    for name in ("authorize","baseline","evaluate","close","status","resume"):
        sub=commands.add_parser(name);sub.add_argument("--json",action="store_true")
        if name=="status":sub.add_argument("--next",action="store_true")
    check=commands.add_parser("check");check.add_argument("preset_id");check.add_argument("--json",action="store_true")
    attest=commands.add_parser("attest");attest.add_argument("input");attest.add_argument("--json",action="store_true")
    review=commands.add_parser("review");review.add_argument("input",nargs="?");review.add_argument("--human",action="store_true");review.add_argument("--json",action="store_true")
    operation="unknown"
    try:
        args=parser.parse_args(argv);operation=args.operation
        if operation=="task":
            operation="task.create";result=application.create(args.store,args.input,preset_config=args.preset_config)
        else:
            if operation=="review" and (args.human==bool(args.input)):
                raise InvalidArguments()
            result=application.execute(args.store,operation,input_path=getattr(args,"input",None),
                preset_id=getattr(args,"preset_id",None),human=getattr(args,"human",False))
        uncertain = result["state"] == "STALE" or "independence_unknown" in result["reasons"] or "unknown_state" in result["reasons"]
        code=4 if uncertain else 1 if result["decision"]=="NOT_CLAIMABLE" else 0
    except InvalidArguments:
        result=application.envelope(operation,status="invalid_input",reasons=("invalid_arguments",));code=2
    except RuntimeError as error:
        reason=str(error)
        if not reason or len(reason)>96 or any(c not in "abcdefghijklmnopqrstuvwxyz_0123456789" for c in reason):
            reason="invalid_state_or_input"
        environment=reason in ("store_busy","private_store_required","snapshot_limit","snapshot_race","file_limit","unsupported_resource_limits","git_snapshot_incomplete","runtime_identity_limit")
        result=application.envelope(operation,status="environment_error" if environment else "invalid_input",reasons=(reason,));code=3 if environment else 2
    except (ValueError,TypeError,KeyError,OverflowError,RecursionError):
        result=application.envelope(operation,status="invalid_input",reasons=("invalid_state_or_input",));code=2
    except (OSError,subprocess.SubprocessError):
        result=application.envelope(operation,status="environment_error",reasons=("environment_error",));code=3
    except Exception:
        result=application.envelope(operation,status="internal_error",reasons=("internal_failure",));code=3
    print(json.dumps(result,sort_keys=True))
    return code
