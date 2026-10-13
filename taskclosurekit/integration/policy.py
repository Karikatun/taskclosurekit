"""Explicit frozen root policy. Coverage remains a complete bounded byte inventory."""
from pathlib import Path
from ..domain.contract import fields, identity, path
from .._primitives import policy, snapshot
from .._primitives.store import bounded_json
from ..execution.presets import binding_identity, validate_binding, validate_record, REGISTRY

SCHEMA = "taskclosurekit/integration/v1"
SCHEMA_V2 = "taskclosurekit/integration/v2"
ROOT_TYPES = ("source", "dependency", "generated", "cache", "evidence/orchestration", "unknown")

def overlap(a, b):
    return policy.scope_contains(a, (b,)) or policy.scope_contains(b, (a,))

def validate_policy(value, contract, registry_record=None):
    version_two=type(value) is dict and value.get("schema")==SCHEMA_V2
    fields(value, ("schema", "stage_equivalence", "roots", "coverage", *(("capture_budget",) if version_two else ())))
    if value["schema"] not in (SCHEMA,SCHEMA_V2) or value["stage_equivalence"] not in ("exact", "content") or value["coverage"] != ("bounded-host-proof-or-rehash" if version_two else "bounded-full-rehash"):
        raise RuntimeError("unsupported_integration_policy")
    if version_two:
        from .budgets import validate_budget
        validate_budget(value["capture_budget"])
    if type(value["roots"]) is not list or len(value["roots"]) > 100:
        raise RuntimeError("invalid_root_policy")
    registry = validate_record(registry_record, contract) if registry_record else REGISTRY
    if version_two:
        for preset_id in contract.authority.presets:
            preset=registry.get(preset_id)
            write_limit=preset.execution_policy.file_bytes if preset.execution_policy else snapshot.FILE_LIMIT
            if preset.permitted_writes and write_limit>value["capture_budget"]["file_bytes"]:
                raise RuntimeError("runner_writes_exceed_capture_budget")
    inputs = list(contract.sources)
    for preset_id in contract.authority.presets:
        preset = registry.get(preset_id)
        inputs.extend(preset.authority_inputs)
        inputs.extend(p for _, paths in preset.relevant_inputs for p in paths)
    roots = []
    for root in value["roots"]:
        fields(root, ("path", "type", "acceptance", "rationale", "dependency_provenance"))
        name = path(root["path"])
        if root["type"] not in ROOT_TYPES or root["acceptance"] not in ("include", "exclude"):
            raise RuntimeError("invalid_root_policy")
        if type(root["rationale"]) is not str or not 1 <= len(root["rationale"]) <= 1024 or any(ord(c)<32 for c in root["rationale"]):
            raise RuntimeError("root_rationale_required")
        if any(overlap(name, previous) for previous in roots):
            raise RuntimeError("overlapping_roots")
        roots.append(name)
        if root["type"] == "evidence/orchestration":
            raise RuntimeError("evidence_must_be_outside_monitored_roots")
        if root["acceptance"] == "exclude":
            if root["type"] not in ("generated", "cache") or not policy.scope_contains(name, contract.authority.write) or any(overlap(name, item) for item in inputs):
                raise RuntimeError("unsafe_acceptance_exclusion")
            if "git-index-whitespace-v1" in contract.authority.presets:
                raise RuntimeError("builtin_requires_exact_inputs")
        provenance = root["dependency_provenance"]
        if root["type"] == "dependency":
            fields(provenance, ("method", "identity_inputs"))
            if provenance["method"] not in (("rehash","immutable-host") if version_two else ("rehash",)):
                raise RuntimeError("immutable_dependency_proof_unavailable")
            if provenance["method"]=="immutable-host" and (not policy.scope_contains(name,contract.authority.read) or any(overlap(name,w) for w in contract.authority.write)):
                raise RuntimeError("immutable_root_mutable_or_unreadable_scope")
            if type(provenance["identity_inputs"]) is not list or not 1 <= len(provenance["identity_inputs"]) <= 100:
                raise RuntimeError("dependency_provenance_required")
            for item in provenance["identity_inputs"]:
                item = path(item)
                if not policy.scope_contains(item, contract.authority.read):
                    raise RuntimeError("dependency_provenance_outside_read_scope")
        elif provenance is not None:
            raise RuntimeError("invalid_dependency_provenance")
    if value["stage_equivalence"] == "content" and "git-index-whitespace-v1" in contract.authority.presets:
        raise RuntimeError("builtin_requires_exact_inputs")
    return value

def prepare_config(config_path, contract, store, registry_record=None, dependency_provider=None):
    config_path = snapshot.safe_path(config_path)
    if snapshot.path_within(config_path, contract.repository) or snapshot.path_within(config_path, store.path):
        raise RuntimeError("integration_config_must_be_outside_repository_and_store")
    binding = binding_identity(config_path)
    value = validate_policy(bounded_json(config_path), contract, registry_record)
    if binding_identity(config_path) != binding:
        raise RuntimeError("integration_config_race")
    record={"schema":value["schema"], "config_path":str(config_path), "config_binding":binding, "policy":value}
    if value["schema"]==SCHEMA_V2:
        from .dependencies import prepare
        record["dependency_bindings"]=prepare(value["roots"],contract,value["capture_budget"],dependency_provider)
    return record

def validate_record_config(record, contract, registry_record=None):
    fields(record, ("schema", "config_path", "config_binding", "policy", *(("dependency_bindings",) if record.get("schema")==SCHEMA_V2 else ())))
    if record["schema"] not in (SCHEMA,SCHEMA_V2) or record["schema"]!=record["policy"]["schema"]:
        raise RuntimeError("unsupported_integration_policy")
    path(record["config_path"], absolute=True); validate_binding(record["config_binding"])
    validate_policy(record["policy"], contract, registry_record)
    if record["schema"]==SCHEMA_V2:
        from .dependencies import validate_bindings
        validate_bindings(record["dependency_bindings"],record)
    return record

def observe(record):
    try:
        binding = binding_identity(record["config_path"])
    except FileNotFoundError:
        binding = {"missing":True}
    return {"policy_digest":identity(record), "config_binding":binding}

def validate_observation(value, record):
    fields(value, ("policy_digest", "config_binding"))
    if value["policy_digest"] != identity(record):
        raise RuntimeError("invalid_integration_binding")
    if value["config_binding"] != {"missing":True}:
        validate_binding(value["config_binding"])

def task_authority_digest(contract_digest, registry_record=None, integration_record=None):
    from ..execution.presets import authority_digest
    base = authority_digest(contract_digest, registry_record)
    return identity({"authority":base,"integration":identity(integration_record)}) if integration_record else base

def excluded(name, record):
    return any(root["acceptance"] == "exclude" and policy.scope_contains(name, (root["path"],)) for root in record["policy"]["roots"])

def semantic_repository(raw, record):
    """All bytes are inspected; excluded outputs do not become test inputs.

    Content-stage mode transfers only after comparing all source bytes/modes,
    frozen execution/authority, index content and substantial Git controls.
    """
    entries = {name:entry for name,entry in raw["entries"].items() if not excluded(name, record)}
    controls = dict(raw["controls"])
    if record["policy"]["stage_equivalence"] == "content":
        # Commit/object/reflog/index stat metadata are operational identities.
        # Source/index content, config, hooks, attributes, ignore and layout remain.
        volatile = (".git/objects", ".git/common/objects", ".git/logs", ".git/common/logs")
        special = {".git/HEAD", ".git/common/HEAD", ".git/index", ".git/COMMIT_EDITMSG", ".git/common/COMMIT_EDITMSG"}
        head_ref=controls.get("head_ref")
        if head_ref:
            special.add((".git/common/" if controls["git_layout"]["kind"]=="linked" else ".git/")+head_ref)
        entries = {n:e for n,e in entries.items() if n not in special and not any(n==p or n.startswith(p+"/") for p in volatile)}
        controls = {k:v for k,v in controls.items() if k not in ("head", "refs", "stages", "flags", "index_entries")}
    return {"entries":entries,"controls":controls}

def permitted_change(contract, baseline, current, record):
    if current.get("integration", {}).get("config_binding") != record["config_binding"]:
        return "integration_authority_changed"
    if any(baseline[key] != current[key] for key in ("source_bindings", "execution", "contract_input")):
        return "authority_changed"
    b, c = semantic_repository(baseline, record), semantic_repository(current, record)
    if baseline["controls"].get("git_layout") != current["controls"].get("git_layout"):
        return "authority_changed"
    if baseline["controls"].get("head_ref") != current["controls"].get("head_ref"):
        return "authority_changed"
    for name in set(baseline["entries"]) | set(current["entries"]):
        if baseline["entries"].get(name) == current["entries"].get(name):
            continue
        if Path(name).name.lower()==".gitattributes":
            return "authority_changed"
        if name.startswith(".git/"):
            if b["entries"].get(name) != c["entries"].get(name):
                return "authority_changed"
        elif not policy.scope_contains(name, contract.authority.write):
            return "write_scope_violation"
    for name in set(baseline["controls"]["index_entries"]) | set(current["controls"]["index_entries"]):
        if baseline["controls"]["index_entries"].get(name) != current["controls"]["index_entries"].get(name):
            if Path(name).name.lower()==".gitattributes":
                return "authority_changed"
            if not policy.scope_contains(name, contract.authority.write):
                return "write_scope_violation"
    if record["policy"]["stage_equivalence"] == "exact" and baseline["controls"]["head"] != current["controls"]["head"]:
        return "authority_changed"
    return None
