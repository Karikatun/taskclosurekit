"""Explicit trusted configuration, frozen per transaction; contracts select IDs only."""
from dataclasses import dataclass
from pathlib import Path
import time
from taskproof import runner, snapshot, core
from taskproof.store import bounded_json
from ..domain.contract import fields, identity, identifier, path, paths
from .dispatch import validate_dispatch

CONFIG_SCHEMA = "taskclosurekit/presets/v1"
INPUT_CATEGORIES = ("source", "tests", "manifests", "lockfiles", "config")
ENVIRONMENT_NAMES = {"PATH", "LC_ALL", "LANG", "PYTHONDONTWRITEBYTECODE", "CI", "NODE_NO_WARNINGS", "TMPDIR"}
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

    def definition(self):
        return {"id": self.id, "executable": self.executable, "argv": list(self.argv),
                "cwd_rule": self.cwd_rule, "environment_allowlist": dict(self.environment_allowlist),
                "timeout": self.timeout, "output_limit": self.output_limit,
                "permitted_writes": list(self.permitted_writes),
                "relevant_inputs": {name: list(items) for name, items in self.relevant_inputs},
                "authority_inputs": list(self.authority_inputs), "runtime_inputs": list(self.runtime_inputs)}

    @property
    def digest(self):
        if self.id == runner.PRESET:
            # Preserve the legacy v2 builtin digest; updating code makes environment stale separately.
            return identity({"id":self.id,"executable":self.executable,"argv":self.argv,"cwd":self.cwd_rule,
                             "env":self.environment_allowlist,"timeout":self.timeout,"output":self.output_limit,
                             "writes":self.permitted_writes})
        return identity(self.definition())


def parse_preset(value):
    fields(value, ("id", "executable", "argv", "cwd_rule", "environment_allowlist", "timeout", "output_limit",
                   "permitted_writes", "relevant_inputs", "authority_inputs", "runtime_inputs"))
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
            ("PATH" in env and env["PATH"] != "/usr/bin:/bin")):
        raise RuntimeError("invalid_preset_environment")
    if (value["cwd_rule"] != "contract-repository" or type(value["timeout"]) not in (int, float) or
            not 0 < value["timeout"] <= runner.TIMEOUT or type(value["output_limit"]) is not int or
            not 0 < value["output_limit"] <= runner.OUTPUT_LIMIT):
        raise RuntimeError("invalid_runner_limits")
    fields(value["relevant_inputs"], INPUT_CATEGORIES)
    relevant = tuple((name, paths(value["relevant_inputs"][name], True)) for name in INPUT_CATEGORIES)
    if not any(items for _, items in relevant):
        raise RuntimeError("missing_relevant_inputs")
    if "TMPDIR" in env:
        temp_path = path(env["TMPDIR"])
        if not core.scope_contains(temp_path, paths(value["permitted_writes"], True)):
            raise RuntimeError("preset_temp_outside_writes")
    runtime = value["runtime_inputs"]
    if type(runtime) is not list or len(runtime) > 16 or len(set(runtime)) != len(runtime):
        raise RuntimeError("invalid_runtime_inputs")
    runtime = tuple(path(item, absolute=True) for item in runtime)
    return Preset(preset_id, executable, tuple(argv), value["cwd_rule"], tuple(sorted(env.items())),
                  value["timeout"], value["output_limit"], paths(value["permitted_writes"], True),
                  relevant, paths(value["authority_inputs"], True), runtime)


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
            type(entry["size"]) is not int or entry["size"] < 0 or not core.hash_string(entry["sha256"])):
        raise RuntimeError("invalid_preset_binding")
    if value["entry"]["kind"] != "file" or value["entry"]["size"] > (RESOURCE_FILE_LIMIT if resource else snapshot.FILE_LIMIT):
        raise RuntimeError("invalid_preset_binding")
    fields(value["physical"], ("device", "inode"))
    if any(type(x) is not int or x < 0 for x in value["physical"].values()):
        raise RuntimeError("invalid_preset_binding")


def scope_overlap(left, right):
    return core.scope_contains(left, (right,)) or core.scope_contains(right, (left,))


def validate_authority(contract, registry):
    for preset_id in contract.authority.presets:
        preset = registry.get(preset_id)
        for item in (*preset.authority_inputs, *(p for _, items in preset.relevant_inputs for p in items)):
            if not core.scope_contains(item, contract.authority.read):
                raise RuntimeError("preset_input_outside_read_scope")
        for item in preset.permitted_writes:
            if not core.scope_contains(item, contract.authority.write):
                raise RuntimeError("preset_write_outside_authority")
            if any(scope_overlap(item, inp) for inp in (*preset.authority_inputs, *(p for _, items in preset.relevant_inputs for p in items))):
                raise RuntimeError("preset_write_overlaps_input")
        if any(scope_overlap(inp, write) for inp in preset.authority_inputs for write in contract.authority.write):
            raise RuntimeError("preset_authority_mutable")


def prepare_config(config_path, contract, store):
    config_path = snapshot.safe_path(config_path)
    if snapshot.path_within(config_path, contract.repository) or snapshot.path_within(config_path, store.path):
        raise RuntimeError("preset_config_must_be_outside_repository_and_store")
    config_binding = binding_identity(config_path)
    value = bounded_json(config_path)
    fields(value, ("schema", "presets"))
    if value["schema"] != CONFIG_SCHEMA or type(value["presets"]) is not list or not 1 <= len(value["presets"]) <= 32:
        raise RuntimeError("invalid_preset_configuration")
    registry = PresetRegistry(tuple(parse_preset(item) for item in value["presets"]))
    validate_authority(contract, registry)
    definitions = [registry.get(p).definition() for p in contract.authority.presets if p != runner.PRESET]
    selected = PresetRegistry(tuple(parse_preset(item) for item in definitions))
    budget = ResourceBudget()
    runtime = {}
    authority = {}
    entries = snapshot.inventory(contract.repository)
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
    record = {"schema": CONFIG_SCHEMA, "presets": definitions, "config_path": str(config_path),
              "config_binding": config_binding, "authority_bindings": authority, "runtime_bindings": runtime}
    validate_record(record, contract)
    return record


def validate_record(record, contract):
    fields(record, ("schema", "presets", "config_path", "config_binding", "authority_bindings", "runtime_bindings"))
    if record["schema"] != CONFIG_SCHEMA or type(record["presets"]) is not list or len(record["presets"]) > 32:
        raise RuntimeError("invalid_preset_configuration")
    registry = PresetRegistry(tuple(parse_preset(item) for item in record["presets"]))
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
    return {"registry_digest": identity(record), "config_binding": read_or_missing(record["config_path"]),
            "authority_bindings": authority,
            "runtime_bindings": {item: read_or_missing(item, True) for item in record["runtime_bindings"]}}


def authority_digest(contract_digest, record):
    return identity({"contract": contract_digest, "registry": identity(record)}) if record else contract_digest
