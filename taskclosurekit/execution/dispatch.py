"""Bounded supported launcher grammars; no execution or dependency inference."""
from pathlib import Path
import re
from taskproof import snapshot, core
from taskproof.store import bounded_json
from ..domain.contract import path


def operand(args, index):
    if index >= len(args) or not args[index]:
        raise RuntimeError("unsupported_dispatch_arguments")
    return args[index], index + 1


def python_target(args):
    index = 0
    local_module_search = True
    while index < len(args):
        arg = args[index]
        index += 1
        if arg == "--":
            target, index = operand(args, index)
            return "script", target
        if arg == "--check-hash-based-pycs":
            value, index = operand(args, index)
            if value not in ("default", "always", "never"):
                raise RuntimeError("unsupported_dispatch_arguments")
            continue
        if arg.startswith("--check-hash-based-pycs="):
            if arg.split("=", 1)[1] not in ("default", "always", "never"):
                raise RuntimeError("unsupported_dispatch_arguments")
            continue
        if not arg.startswith("-"):
            return "script", arg
        if arg == "-" or arg.startswith("--"):
            raise RuntimeError("unsupported_dispatch_arguments")
        options = arg[1:]
        cursor = 0
        while cursor < len(options):
            flag = options[cursor]
            cursor += 1
            if flag in "WXcm":
                value = options[cursor:]
                if not value:
                    value, index = operand(args, index)
                if flag in "cm":
                    if flag == "m" and not local_module_search:
                        raise RuntimeError("unsupported_dispatch_arguments")
                    return ("inline" if flag == "c" else "module"), value
                break
            if flag not in "bBdEiIOPqsSuv":
                raise RuntimeError("unsupported_dispatch_arguments")
            if flag in "IP":
                local_module_search = False
    raise RuntimeError("preset_launch_target_required")


def bind_script(target, preset, entries, repository):
    if target.startswith("/"):
        target_path = snapshot.safe_path(path(target, absolute=True))
        if repository and snapshot.path_within(target_path, repository):
            target = str(target_path.relative_to(snapshot.safe_path(repository)))
        else:
            if str(target_path) not in preset.runtime_inputs or not target_path.is_file():
                raise RuntimeError("preset_launch_target_required")
            return
    target = path(target)
    if target not in preset.authority_inputs:
        raise RuntimeError("undeclared_command_authority")
    if entries.get(target, {}).get("kind") != "file":
        raise RuntimeError("preset_launch_target_required")


def bind_module(module, preset, entries, write_scope):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", module):
        raise RuntimeError("unsupported_dispatch_arguments")
    base = module.replace(".", "/")
    file_target = base + ".py"
    package_target = base + "/__main__.py"
    file_exists = entries.get(file_target, {}).get("kind") == "file"
    package_exists = entries.get(package_target, {}).get("kind") == "file"
    # Global/site module resolution is intentionally unsupported: missing local
    # targets cannot become a mutable shadow module after CREATE.
    if file_exists == package_exists:
        raise RuntimeError("preset_launch_target_required")
    # A future package takes precedence over an existing module file. Do not
    # authorize that competing dispatch target implicitly through write scope.
    if file_exists and (base + "/__init__.py" in entries or
                        core.scope_contains(base + "/__init__.py", write_scope)):
        raise RuntimeError("preset_launch_target_required")
    targets = [file_target if file_exists else package_target]
    parts = base.split("/")
    for length in range(1, len(parts) + (1 if package_exists else 0)):
        targets.append("/".join(parts[:length]) + "/__init__.py")
    if any(target not in preset.authority_inputs for target in targets):
        raise RuntimeError("undeclared_command_authority")
    if any(entries.get(target, {}).get("kind") != "file" for target in targets):
        raise RuntimeError("preset_launch_target_required")


def node_dispatch(args, preset, entries, repository):
    index = 0
    test_mode = False
    inline_mode = False
    simple = {"--no-warnings", "--enable-source-maps", "--test-only", "--test-force-exit",
              "--experimental-test-coverage", "--trace-warnings", "--use-strict"}
    values = {"--test-name-pattern", "--test-skip-pattern", "--test-concurrency", "--test-timeout"}
    loaders = {"--require", "--import", "--loader", "--experimental-loader"}
    while index < len(args):
        arg = args[index]; index += 1
        if arg == "--":
            if test_mode or inline_mode:
                break
            target, _ = operand(args, index)
            bind_script(target, preset, entries, repository)
            return
        if not arg.startswith("-"):
            if test_mode or inline_mode:
                continue
            bind_script(arg, preset, entries, repository)
            return
        if arg == "--test":
            test_mode = True
            continue
        if arg in simple:
            continue
        if arg in ("-e", "--eval", "-p", "--print"):
            _, index = operand(args, index)
            inline_mode = True
            continue
        name, equals, attached = arg.partition("=")
        if name in loaders or arg == "-r" or arg.startswith("-r") and len(arg) > 2:
            target = attached if equals else arg[2:] if arg.startswith("-r") and len(arg) > 2 else ""
            if not target:
                target, index = operand(args, index)
            # Bare specifiers use package resolution, rather than the local
            # file with the same spelling. Only explicit file loaders supported.
            if target.startswith("./"):
                target = target[2:]
            elif not target.startswith("/"):
                raise RuntimeError("unsupported_dispatch_arguments")
            bind_script(target, preset, entries, repository)
            continue
        if name in values or name == "--test-reporter":
            value = attached if equals else ""
            if not value:
                value, index = operand(args, index)
            if name == "--test-reporter" and value not in ("spec", "tap", "dot", "junit", "lcov"):
                bind_script(value, preset, entries, repository)
            continue
        raise RuntimeError("unsupported_dispatch_arguments")
    if inline_mode:
        return
    if not test_mode:
        raise RuntimeError("preset_launch_target_required")
    if not dict(preset.relevant_inputs).get("tests"):
        raise RuntimeError("missing_relevant_inputs")


def package_dispatch(command, args, preset, entries, repository, write_scope):
    index = 0
    cwd = ""
    while index < len(args) and args[index].startswith("-"):
        arg = args[index]; index += 1
        name, equals, attached = arg.partition("=")
        if command != "bun" or name != "--cwd":
            raise RuntimeError("unsupported_dispatch_arguments")
        if equals:
            value = attached
        else:
            value, index = operand(args, index)
        cwd = path(value) + "/"
    mode, index = operand(args, index)
    if command == "bun" and mode in ("test", "build"):
        category = "tests" if mode == "test" else "source"
        if not dict(preset.relevant_inputs).get(category):
            raise RuntimeError("missing_relevant_inputs")
        implicit = cwd + "bunfig.toml"
        if implicit in entries:
            bind_script(implicit, preset, entries, repository)
        elif core.scope_contains(implicit, write_scope):
            raise RuntimeError("preset_launch_target_required")
        simple = {"--coverage", "--watch", "--smol", "--todo", "--only", "--minify", "--sourcemap", "--compile"}
        values = {"--timeout", "--test-name-pattern", "--reporter", "--coverage-reporter",
                  "--target", "--outfile", "--outdir", "--external"}
        while index < len(args):
            arg = args[index]; index += 1
            if arg == "--":
                break
            if not arg.startswith("-"):
                continue
            if arg in simple:
                continue
            name, equals, attached = arg.partition("=")
            if name in ("--preload", "--config", "--plugin", "-r"):
                target = attached if equals else ""
                if not target:
                    target, index = operand(args, index)
                bind_script(cwd + target if not target.startswith("/") else target, preset, entries, repository)
            elif name in values:
                if not equals:
                    _, index = operand(args, index)
                elif not attached:
                    raise RuntimeError("unsupported_dispatch_arguments")
            else:
                raise RuntimeError("unsupported_dispatch_arguments")
        return
    if mode == "run" or command != "bun" and mode in ("test", "start", "stop", "restart"):
        manifest = cwd + "package.json"
        if manifest not in preset.authority_inputs:
            raise RuntimeError("undeclared_command_authority")
        if entries.get(manifest, {}).get("kind") != "file":
            raise RuntimeError("preset_launch_target_required")
        script = mode
        direct = False
        if mode == "run":
            script, index = operand(args, index)
            if script.startswith("-"):
                raise RuntimeError("unsupported_dispatch_arguments")
            if command == "bun" and ("/" in script or Path(script).suffix in (".js", ".mjs", ".cjs", ".ts", ".tsx")):
                bind_script(cwd + script if not script.startswith("/") else script, preset, entries, repository)
                direct = True
        # A manager option after the script can alter package resolution. Only
        # the explicit argument delimiter is admitted for script arguments.
        if index < len(args) and args[index] != "--":
            raise RuntimeError("unsupported_dispatch_arguments")
        if not direct:
            if repository is None:
                raise RuntimeError("preset_launch_target_required")
            value = bounded_json(snapshot.safe_path(repository) / manifest)
            scripts = value.get("scripts") if type(value) is dict else None
            if type(scripts) is not dict or type(scripts.get(script)) is not str or not scripts[script]:
                raise RuntimeError("preset_launch_target_required")
        return
    if command == "bun":
        bind_script(cwd + mode, preset, entries, repository)
        return
    raise RuntimeError("unsupported_dispatch_arguments")


def validate_dispatch(preset, entries, repository=None, write_scope=()):
    command = Path(preset.executable).name.lower()
    if command.startswith("python"):
        kind, target = python_target(preset.argv)
        if kind == "script":
            bind_script(target, preset, entries, repository)
        elif kind == "module":
            bind_module(target, preset, entries, write_scope)
        return
    if command == "node":
        node_dispatch(preset.argv, preset, entries, repository)
    elif command in ("bun", "npm", "pnpm", "yarn"):
        package_dispatch(command, preset.argv, preset, entries, repository, write_scope)
    elif command in ("sh", "bash", "dash", "zsh", "ruby", "perl", "npx", "env"):
        raise RuntimeError("unsupported_dispatch_arguments")
    # Native fixed executables consume data arguments; indirect command/resource
    # declarations remain trusted configuration responsibilities.
