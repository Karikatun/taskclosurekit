"""Explicit trusted configuration, frozen per transaction; contracts select IDs only."""
from dataclasses import dataclass
from pathlib import Path
import os
import stat
import time
from .._primitives import runner, snapshot, policy
from .._primitives.store import bounded_json
from ..domain.contract import fields, identity, identifier, path, paths
from .dispatch import validate_dispatch
from .policy import parse_policy, validate_policy_record

CONFIG_SCHEMA = "taskclosurekit/presets/v1"
CONFIG_SCHEMA_V2 = "taskclosurekit/presets/v2"
INPUT_CATEGORIES = ("source", "tests", "manifests", "lockfiles", "config")
ENVIRONMENT_NAMES = {"PATH", "LC_ALL", "LANG", "PYTHONDONTWRITEBYTECODE", "CI", "NODE_NO_WARNINGS", "TMPDIR"}
PACKAGE_DATA_FIELDS = {"description", "version", "keywords", "license", "author", "contributors", "homepage", "bugs", "repository", "funding", "private"}
# Executable/resource hashing has its own explicit budget. Repository IO limits stay unchanged.
RESOURCE_FILE_LIMIT = 384 * 1024 * 1024
RESOURCE_TOTAL_LIMIT = 1024 * 1024 * 1024
RESOURCE_COUNT_LIMIT = 32
RESOURCE_SECONDS = 10.0

@dataclass(frozen=True)
class Preset:
    id: str
    executable: str
    argv: tuple
    cwd_rule: str
    environment_allowlist: tuple
    timeout: float
    output_limit: int
    permitted_writes: tuple
    relevant_inputs: tuple = ()
    authority_inputs: tuple = ()
    runtime_inputs: tuple = ()
    execution_policy: object = None
    dispatcher_inputs: tuple = ()

    def definition(self):
        result = {"id": self.id, "executable": self.executable, "argv": list(self.argv),
                "cwd_rule": self.cwd_rule, "environment_allowlist": dict(self.environment_allowlist),
                "timeout": self.timeout, "output_limit": self.output_limit,
                "permitted_writes": list(self.permitted_writes),
                "relevant_inputs": {name: list(items) for name, items in self.relevant_inputs},
                "authority_inputs": list(self.authority_inputs), "runtime_inputs": list(self.runtime_inputs)}
        if self.execution_policy:
            result["dispatcher_inputs"] = [{"path": item, "kind": kind, "mutable_fields": list(mutable)} for item, kind, mutable in self.dispatcher_inputs]
        return result

    @property
    def digest(self):
        if self.id == runner.PRESET:
            # Preserve the legacy v2 builtin digest; updating code makes environment stale separately.
            return identity({"id":self.id,"executable":self.executable,"argv":self.argv,"cwd":self.cwd_rule,
                             "env":self.environment_allowlist,"timeout":self.timeout,"output":self.output_limit,
                             "writes":self.permitted_writes})
        return identity({**self.definition(), **({"execution_policy": self.execution_policy.definition()} if self.execution_policy else {})})


def parse_preset(value, execution_policy=None):
    fields(value, ("id", "executable", "argv", "cwd_rule", "environment_allowlist", "timeout", "output_limit",
                   "permitted_writes", "relevant_inputs", "authority_inputs", "runtime_inputs", *( ("dispatcher_inputs",) if execution_policy and "dispatcher_inputs" in value else ())))
    preset_id = identifier(value["id"])
    if preset_id == runner.PRESET:
        raise RuntimeError("builtin_preset_override")
    executable = path(value["executable"], absolute=True)
    argv = value["argv"]
    if (type(argv) is not list or len(argv) > 64 or any(type(arg) is not str or len(arg) > 2048 or
            any(ord(c) < 32 for c in arg) for arg in argv)):
        raise RuntimeError("invalid_preset_argv")
    env = value["environment_allowlist"]
    if (type(env) is not dict or set(env) - ENVIRONMENT_NAMES or any(type(v) is not str or len(v) > 1024 or
            any(ord(c) < 32 for c in v) for v in env.values()) or
            ("PATH" in env and env["PATH"] != "/usr/bin:/bin" and execution_policy is None)):
        raise RuntimeError("invalid_preset_environment")
    if execution_policy and "PATH" in env:
        segments = env["PATH"].split(":")
        if len(segments) > 16 or len(segments) != len(set(segments)):
            raise RuntimeError("invalid_preset_environment")
        for directory in segments:
            path(directory, absolute=True)
    if (value["cwd_rule"] != "contract-repository" or type(value["timeout"]) not in (int, float) or
            not 0 < value["timeout"] <= (execution_policy.timeout_limit if execution_policy else runner.TIMEOUT) or type(value["output_limit"]) is not int or
            not 0 < value["output_limit"] <= (execution_policy.output_limit if execution_policy else runner.OUTPUT_LIMIT)):
        raise RuntimeError("invalid_runner_limits")
    fields(value["relevant_inputs"], INPUT_CATEGORIES)
    relevant = tuple((name, paths(value["relevant_inputs"][name], True)) for name in INPUT_CATEGORIES)
    if not any(items for _, items in relevant):
        raise RuntimeError("missing_relevant_inputs")
    if "TMPDIR" in env:
        temp_path = path(env["TMPDIR"])
        if not policy.scope_contains(temp_path, paths(value["permitted_writes"], True)):
            raise RuntimeError("preset_temp_outside_writes")
    runtime = value["runtime_inputs"]
    if type(runtime) is not list or len(runtime) > 16 or len(set(runtime)) != len(runtime):
        raise RuntimeError("invalid_runtime_inputs")
    runtime = tuple(path(item, absolute=True) for item in runtime)
    dispatchers = value.get("dispatcher_inputs", [])
    if type(dispatchers) is not list or len(dispatchers) > 16:
        raise RuntimeError("invalid_dispatcher_inputs")
    parsed_dispatchers = []
    for dispatcher in dispatchers:
        fields(dispatcher, ("path", "kind", "mutable_fields"))
        mutable = dispatcher["mutable_fields"]
        if (dispatcher["kind"] != "package-scripts/v1" or type(mutable) is not list or
                any(type(name) is not str or name not in PACKAGE_DATA_FIELDS for name in mutable) or
                len(mutable) != len(set(mutable))):
            raise RuntimeError("unsupported_dispatcher_projection")
        parsed_dispatchers.append((path(dispatcher["path"]), dispatcher["kind"], tuple(sorted(mutable))))
    if len({item for item, _, _ in parsed_dispatchers}) != len(parsed_dispatchers):
        raise RuntimeError("invalid_dispatcher_inputs")
    return Preset(preset_id, executable, tuple(argv), value["cwd_rule"], tuple(sorted(env.items())),
                  value["timeout"], value["output_limit"], paths(value["permitted_writes"], True),
                  relevant, paths(value["authority_inputs"], True), runtime, execution_policy, tuple(parsed_dispatchers))


class PresetRegistry:
    def __init__(self, presets=()):
        builtin = Preset(runner.PRESET, "validated-system-git", runner.CHECK_ARGS, "contract-repository",
                         tuple(sorted(runner.environment().items())), runner.TIMEOUT, runner.OUTPUT_LIMIT, ())
        self._presets = {builtin.id: builtin}
        for preset in presets:
            if preset.id in self._presets:
                raise RuntimeError("duplicate_preset")
            self._presets[preset.id] = preset

    def get(self, preset_id):
        if type(preset_id) is not str or preset_id not in self._presets:
            raise RuntimeError("unknown_preset")
        return self._presets[preset_id]


REGISTRY = PresetRegistry()


def physical(file_path):
    info = file_path.lstat()
    return {"device": info.st_dev, "inode": info.st_ino}


class ResourceBudget:
    def __init__(self):
        self.bytes = 0
        self.count = 0
        self.started = time.monotonic()

    def check(self):
        if self.bytes > RESOURCE_TOTAL_LIMIT or self.count > RESOURCE_COUNT_LIMIT or time.monotonic() - self.started > RESOURCE_SECONDS:
            raise RuntimeError("runtime_identity_limit")


def resource_identity(file_path, budget):
    file_path = snapshot.safe_path(file_path)
    budget.count += 1
    before = physical(file_path)
    entry, _ = snapshot.regular(file_path, budget, RESOURCE_FILE_LIMIT)
    if physical(file_path) != before:
        raise RuntimeError("snapshot_race")
    return {"entry": entry, "physical": before}


def binding_identity(file_path):
    file_path = snapshot.safe_path(file_path)
    before = physical(file_path)
    entry = snapshot.regular(file_path, snapshot.Budget(), 65536)[0]
    if physical(file_path) != before:
        raise RuntimeError("snapshot_race")
    return {"entry": entry, "physical": before}


def validate_binding(value, resource=False):
    fields(value, ("entry", "physical"))
    entry = value["entry"]
    fields(entry, ("kind", "mode", "size", "sha256"))
    if (type(entry["mode"]) is not int or not 0 <= entry["mode"] <= 0o7777 or
            type(entry["size"]) is not int or entry["size"] < 0 or not policy.hash_string(entry["sha256"])):
        raise RuntimeError("invalid_preset_binding")
    if value["entry"]["kind"] != "file" or value["entry"]["size"] > (RESOURCE_FILE_LIMIT if resource else snapshot.FILE_LIMIT):
        raise RuntimeError("invalid_preset_binding")
    fields(value["physical"], ("device", "inode"))
    if any(type(x) is not int or x < 0 for x in value["physical"].values()):
        raise RuntimeError("invalid_preset_binding")


def scope_overlap(left, right):
    return policy.scope_contains(left, (right,)) or policy.scope_contains(right, (left,))


def validate_authority(contract, registry):
    for preset_id in contract.authority.presets:
        preset = registry.get(preset_id)
        for item in (*preset.authority_inputs, *(p for p, _, _ in preset.dispatcher_inputs), *(p for _, items in preset.relevant_inputs for p in items)):
            if not policy.scope_contains(item, contract.authority.read):
                raise RuntimeError("preset_input_outside_read_scope")
        for item in preset.permitted_writes:
            if not policy.scope_contains(item, contract.authority.write):
                raise RuntimeError("preset_write_outside_authority")
            if any(scope_overlap(item, inp) for inp in (*preset.authority_inputs, *(p for p, _, _ in preset.dispatcher_inputs), *(p for _, items in preset.relevant_inputs for p in items))):
                raise RuntimeError("preset_write_overlaps_input")
        if not preset.execution_policy and any(scope_overlap(inp, write) for inp in preset.authority_inputs for write in contract.authority.write):
            raise RuntimeError("preset_authority_mutable")
        if any(item in preset.authority_inputs for item, _, _ in preset.dispatcher_inputs):
            raise RuntimeError("ambiguous_dispatcher_binding")


def dispatcher_bindings(presets, repository, entries):
    result = {}
    for preset in presets:
        command = Path(preset.executable).name.lower()
        for item, kind, mutable in preset.dispatcher_inputs:
            if command not in ("bun", "npm", "pnpm", "yarn"):
                raise RuntimeError("unsupported_dispatcher_projection")
            if entries.get(item, {}).get("kind") != "file":
                raise RuntimeError("preset_launch_target_required")
            file_path = snapshot.safe_path(Path(repository) / item)
            before = binding_identity(file_path)
            value = bounded_json(file_path)
            if type(value) is not dict or type(value.get("scripts")) is not dict:
                raise RuntimeError("unsupported_dispatcher_projection")
            if binding_identity(file_path) != before or before["entry"] != entries[item]:
                raise RuntimeError("snapshot_race")
            # Only explicitly selected known metadata fields are mutable data.
            # Execution controls and every unrecognized field remain frozen.
            projected = {name: data for name, data in value.items() if name not in mutable}
            binding = {"kind": kind, "projection_digest": identity({"frozen": projected, "mutable_fields": list(mutable)}), "physical": before["physical"]}
            if item in result and result[item] != binding:
                raise RuntimeError("ambiguous_dispatcher_binding")
            result[item] = binding
    return result


def validate_dispatcher_binding(value):
    fields(value, ("kind", "projection_digest", "physical"))
    if value["kind"] != "package-scripts/v1" or not policy.hash_string(value["projection_digest"]):
        raise RuntimeError("invalid_dispatcher_binding")
    fields(value["physical"], ("device", "inode"))
    if any(type(number) is not int or number < 0 for number in value["physical"].values()):
        raise RuntimeError("invalid_dispatcher_binding")


def path_binding(presets, runtime, repository, store_path):
    """Bind declared executable names' actual first PATH match, never ambient PATH.

    This is declared runtime coverage, not a discovery of indirect process graphs.
    """
    directories = {}
    resolutions = {}
    for preset in presets:
        if preset.id == runner.PRESET:
            continue
        segments = dict(preset.environment_allowlist).get("PATH", "").split(":")
        segments = segments if segments != [""] else []
        for directory in segments:
            observed = snapshot.safe_path(directory)
            if (snapshot.path_within(observed, repository) or store_path is not None and snapshot.path_within(observed, store_path)
                    or not stat.S_ISDIR(observed.lstat().st_mode)):
                raise RuntimeError("toolchain_path_must_be_external_directory")
            directories[directory] = physical(observed)
        commands = {}
        for file_path in (preset.executable, *preset.runtime_inputs):
            if runtime[file_path]["entry"]["mode"] & 0o111:
                name = Path(file_path).name
                if name in commands and commands[name] != file_path:
                    raise RuntimeError("ambiguous_runtime_command")
                commands[name] = file_path
        resolutions[preset.id] = {}
        for name, approved in commands.items():
            for directory in segments:
                candidate = Path(directory) / name
                try:
                    info = candidate.lstat()
                except FileNotFoundError:
                    continue
                if not info.st_mode & 0o111 or stat.S_ISDIR(info.st_mode):
                    continue
                candidate = snapshot.safe_path(candidate)
                if not stat.S_ISREG(info.st_mode) or str(candidate) != approved:
                    raise RuntimeError("runtime_command_resolution_unbound")
                resolutions[preset.id][name] = str(candidate)
                break
    if any(physical(snapshot.safe_path(directory)) != observed for directory, observed in directories.items()):
        raise RuntimeError("snapshot_race")
    return {"directories": directories, "resolutions": resolutions}


def validate_path_binding(value, presets, runtime):
    fields(value, ("directories", "resolutions"))
    expected = {segment for preset in presets for segment in dict(preset.environment_allowlist).get("PATH", "").split(":") if segment}
    if (type(value["directories"]) is not dict or set(value["directories"]) != expected or
            type(value["resolutions"]) is not dict or set(value["resolutions"]) != {preset.id for preset in presets}):
        raise RuntimeError("invalid_path_binding")
    for directory, physical_identity in value["directories"].items():
        path(directory, absolute=True)
        fields(physical_identity, ("device", "inode"))
        if any(type(number) is not int or number < 0 for number in physical_identity.values()):
            raise RuntimeError("invalid_path_binding")
    for preset in presets:
        resolutions = value["resolutions"][preset.id]
        if type(resolutions) is not dict or len(resolutions) > 32:
            raise RuntimeError("invalid_path_binding")
        allowed = {p for p in (preset.executable, *preset.runtime_inputs) if runtime[p]["entry"]["mode"] & 0o111}
        for name, resolved in resolutions.items():
            if resolved not in allowed or name != Path(resolved).name or str(Path(resolved).parent) not in expected:
                raise RuntimeError("invalid_path_binding")


def validate_shebang_runtimes(presets, runtime, path_bindings):
    """External script interpreters must be explicitly in the frozen runtime set."""
    for preset in presets:
        declared = {preset.executable, *preset.runtime_inputs}
        for file_path in declared:
            if not runtime[file_path]["entry"]["mode"] & 0o111:
                continue
            checked = snapshot.safe_path(file_path)
            fd = os.open(str(checked), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                opened = os.fstat(fd)
                prefix = os.read(fd, 257)
                if (opened.st_dev, opened.st_ino) != (runtime[file_path]["physical"]["device"], runtime[file_path]["physical"]["inode"]):
                    raise RuntimeError("snapshot_race")
            finally:
                os.close(fd)
            if not prefix.startswith(b"#!"):
                continue
            try:
                line = prefix.split(b"\n", 1)[0].decode("ascii")[2:].strip().split()
            except UnicodeDecodeError:
                raise RuntimeError("unbound_shebang_runtime")
            if len(prefix) > 256 and b"\n" not in prefix or not line:
                raise RuntimeError("unbound_shebang_runtime")
            interpreter = line[0]
            if interpreter not in declared or not runtime[interpreter]["entry"]["mode"] & 0o111:
                raise RuntimeError("unbound_shebang_runtime")
            name = Path(interpreter).name.lower()
            if name == "env":
                if len(line) != 2 or line[1].startswith("-") or "/" in line[1]:
                    raise RuntimeError("unsupported_shebang_runtime")
                resolved = path_bindings["resolutions"][preset.id].get(line[1])
                if resolved not in declared:
                    raise RuntimeError("unbound_shebang_runtime")
                name = Path(resolved).name.lower()
            elif len(line) != 1:
                raise RuntimeError("unsupported_shebang_runtime")
            if not (name.startswith("python") or name in ("node", "bun")):
                raise RuntimeError("unsupported_shebang_runtime")


def prepare_config(config_path, contract, store, *, capture_budget=None):
    config_path = snapshot.safe_path(config_path)
    if snapshot.path_within(config_path, contract.repository) or snapshot.path_within(config_path, store.path):
        raise RuntimeError("preset_config_must_be_outside_repository_and_store")
    config_binding = binding_identity(config_path)
    value = bounded_json(config_path)
    schema = value.get("schema") if type(value) is dict else None
    fields(value, ("schema", "presets", "execution_policy") if schema == CONFIG_SCHEMA_V2 else ("schema", "presets"))
    if schema not in (CONFIG_SCHEMA, CONFIG_SCHEMA_V2) or type(value["presets"]) is not list or not 1 <= len(value["presets"]) <= 32:
        raise RuntimeError("invalid_preset_configuration")
    execution_policy = parse_policy(value["execution_policy"]) if schema == CONFIG_SCHEMA_V2 else None
    registry = PresetRegistry(tuple(parse_preset(item, execution_policy) for item in value["presets"]))
    validate_authority(contract, registry)
    definitions = [registry.get(p).definition() for p in contract.authority.presets if p != runner.PRESET]
    selected = PresetRegistry(tuple(parse_preset(item, execution_policy) for item in definitions))
    budget = ResourceBudget()
    runtime = {}
    authority = {}
    entries = snapshot.inventory(contract.repository,snapshot.Budget(capture_budget))
    for preset in (selected.get(p) for p in contract.authority.presets):
        if preset.id != runner.PRESET:
            validate_dispatch(preset, entries, contract.repository, contract.authority.write)
        for file_path in (*preset.runtime_inputs, *((preset.executable,) if preset.id != runner.PRESET else ())):
            if snapshot.path_within(file_path, contract.repository) or snapshot.path_within(file_path, store.path):
                raise RuntimeError("runtime_input_must_be_outside_repository_and_store")
            if file_path not in runtime:
                runtime[file_path] = resource_identity(file_path, budget)
        if preset.id != runner.PRESET and not runtime[preset.executable]["entry"]["mode"] & 0o111:
            raise RuntimeError("preset_executable_required")
        for item in preset.authority_inputs:
            if entries.get(item, {}).get("kind") != "file":
                raise RuntimeError("preset_authority_file_required")
            file_path = snapshot.safe_path(Path(contract.repository) / item)
            observed = binding_identity(file_path)
            if observed["entry"] != entries[item]:
                raise RuntimeError("snapshot_race")
            authority[item] = observed
        for _, items in preset.relevant_inputs:
            for item in items:
                if item not in entries:
                    raise RuntimeError("preset_input_required")
    if binding_identity(config_path) != config_binding:
        raise RuntimeError("preset_config_race")
    record = {"schema": schema, "presets": definitions, "config_path": str(config_path),
              "config_binding": config_binding, "authority_bindings": authority, "runtime_bindings": runtime}
    if execution_policy:
        record["execution_policy"] = execution_policy.definition()
        record["path_bindings"] = path_binding([selected.get(p) for p in contract.authority.presets if p != runner.PRESET], runtime, contract.repository, store.path)
        validate_shebang_runtimes([selected.get(p) for p in contract.authority.presets if p != runner.PRESET], runtime, record["path_bindings"])
        record["dispatcher_bindings"] = dispatcher_bindings([selected.get(p) for p in contract.authority.presets if p != runner.PRESET], contract.repository, entries)
    validate_record(record, contract)
    return record


def validate_record(record, contract):
    schema = record.get("schema") if type(record) is dict else None
    fields(record, ("schema", "presets", "config_path", "config_binding", "authority_bindings", "runtime_bindings", *( ("execution_policy", "path_bindings", "dispatcher_bindings") if schema == CONFIG_SCHEMA_V2 else ())))
    if schema not in (CONFIG_SCHEMA, CONFIG_SCHEMA_V2) or type(record["presets"]) is not list or len(record["presets"]) > 32:
        raise RuntimeError("invalid_preset_configuration")
    execution_policy = validate_policy_record(record["execution_policy"]) if schema == CONFIG_SCHEMA_V2 else None
    registry = PresetRegistry(tuple(parse_preset(item, execution_policy) for item in record["presets"]))
    validate_authority(contract, registry)
    if set(p.id for p in registry._presets.values()) - {runner.PRESET} != set(contract.authority.presets) - {runner.PRESET}:
        raise RuntimeError("preset_selection_mismatch")
    path(record["config_path"], absolute=True)
    validate_binding(record["config_binding"])
    expected_authority = {item for p in record["presets"] for item in p["authority_inputs"]}
    expected_runtime = {item for p in record["presets"] for item in (*p["runtime_inputs"], p["executable"])}
    if (type(record["authority_bindings"]) is not dict or set(record["authority_bindings"]) != expected_authority or
            type(record["runtime_bindings"]) is not dict or set(record["runtime_bindings"]) != expected_runtime or len(expected_runtime) > RESOURCE_COUNT_LIMIT):
        raise RuntimeError("invalid_preset_binding")
    for binding in record["authority_bindings"].values():
        validate_binding(binding)
    for binding in record["runtime_bindings"].values():
        validate_binding(binding, resource=True)
    if execution_policy:
        selected = [registry.get(p) for p in contract.authority.presets if p != runner.PRESET]
        validate_path_binding(record["path_bindings"], selected, record["runtime_bindings"])
        if type(record["dispatcher_bindings"]) is not dict or set(record["dispatcher_bindings"]) != {item for preset in selected for item, _, _ in preset.dispatcher_inputs}:
            raise RuntimeError("invalid_dispatcher_binding")
        for binding in record["dispatcher_bindings"].values():
            validate_dispatcher_binding(binding)
    return registry


def observe(record, contract, entries):
    """No live definition import: changed resources only invalidate pinned authority."""
    budget = ResourceBudget()
    def read_or_missing(file_path, resource=False):
        try:
            return resource_identity(file_path, budget) if resource else binding_identity(file_path)
        except FileNotFoundError:
            return {"missing": True}
    authority = {}
    for item in record["authority_bindings"]:
        authority[item] = ({"entry": entries[item], "physical": physical(snapshot.safe_path(Path(contract.repository) / item))}
                           if entries.get(item, {}).get("kind") == "file" else {"missing": True})
    result = {"registry_digest": identity(record), "config_binding": read_or_missing(record["config_path"]),
            "authority_bindings": authority,
            "runtime_bindings": {item: read_or_missing(item, True) for item in record["runtime_bindings"]}}
    if record["schema"] == CONFIG_SCHEMA_V2:
        registry = validate_record(record, contract)
        try:
            result["path_bindings"] = path_binding([registry.get(p) for p in contract.authority.presets if p != runner.PRESET], result["runtime_bindings"], contract.repository, None)
        except (FileNotFoundError, RuntimeError, KeyError):
            result["path_bindings"] = {"unknown": True}
        try:
            result["dispatcher_bindings"] = dispatcher_bindings([registry.get(p) for p in contract.authority.presets if p != runner.PRESET], contract.repository, entries)
        except (FileNotFoundError, RuntimeError):
            result["dispatcher_bindings"] = {"unknown": True}
    return result


def authority_digest(contract_digest, record):
    return identity({"contract": contract_digest, "registry": identity(record)}) if record else contract_digest
