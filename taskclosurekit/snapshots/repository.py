"""Repository adapter enforces strict inventory/Git/path/bounded IO protections."""
from .._primitives import policy, snapshot
from ..domain.contract import identity
from ..domain.snapshot import Snapshot
from ..execution.presets import REGISTRY, observe, validate_record

PROGRAM_ROOTS = ("taskclosurekit", "tests")

def capture(contract, input_path, registry_record=None):
    raw = snapshot.capture(contract.repository, input_path, contract.sources, program_roots=PROGRAM_ROOTS)
    if registry_record is not None:
        raw["capabilities"] = observe(registry_record, contract, raw["entries"])
    return raw, describe(raw, contract, registry_record)

def describe(raw, contract, registry_record=None):
    digest = snapshot.digest(raw)
    bounded = {key: value for key, value in raw.items() if key != "capabilities"}
    policy.valid_snapshot({"snapshot":bounded, "digest":snapshot.digest(bounded)}, contract.snapshot_policy())
    registry = validate_record(registry_record, contract) if registry_record else REGISTRY
    capability = raw.get("capabilities")
    if registry_record:
        from ..domain.contract import fields
        from ..execution.presets import validate_binding
        fields(capability, ("registry_digest", "config_binding", "authority_bindings", "runtime_bindings"))
        if (capability["registry_digest"] != identity(registry_record) or
                set(capability["authority_bindings"]) != set(registry_record["authority_bindings"]) or
                set(capability["runtime_bindings"]) != set(registry_record["runtime_bindings"])):
            raise RuntimeError("invalid_preset_binding")
        for binding, resource in [(capability["config_binding"], False),
                *((v, False) for v in capability["authority_bindings"].values()),
                *((v, True) for v in capability["runtime_bindings"].values())]:
            if not (type(binding) is dict and set(binding) == {"missing"} and binding["missing"] is True):
                validate_binding(binding, resource=resource)
    elif capability is not None:
        raise RuntimeError("invalid_preset_binding")
    program = raw["execution"]["program"]
    if not all(any(p.startswith(root+"/") for p in program) for root in PROGRAM_ROOTS):
        raise RuntimeError("incomplete_program_identity")
    return Snapshot(digest, identity({"entries":raw["entries"],"controls":raw["controls"]}),
        identity({"contract":contract.digest,"input":raw["contract_input"],"sources":raw["source_bindings"], **({"capabilities":{k:v for k,v in capability.items() if k != "runtime_bindings"}} if capability else {})}),
        identity({"execution":raw["execution"],"presets":[registry.get(p).digest for p in contract.authority.presets], **({"runtime":capability["runtime_bindings"]} if capability else {})}),
        raw["controls"]["head"])

def preset_error(current, registry_record):
    if not registry_record:
        return None
    capability = current["capabilities"]
    if (capability["config_binding"] != registry_record["config_binding"] or
            capability["authority_bindings"] != registry_record["authority_bindings"]):
        return "preset_authority_changed"
    if capability["runtime_bindings"] != registry_record["runtime_bindings"]:
        return "preset_environment_changed"
    return None


def authority_error(contract, baseline, current, expected_input, registry_record=None):
    error = preset_error(current, registry_record)
    if error:
        return error
    if current["contract_input"] != expected_input:
        return "authority_changed"
    try:
        policy.permitted_change(contract.snapshot_policy(), {"snapshot":{k:v for k,v in baseline.items() if k != "capabilities"}}, {k:v for k,v in current.items() if k != "capabilities"})
    except RuntimeError as error:
        reason = str(error)
        if reason == "source_changed" and any(
            not policy.scope_contains(source, contract.authority.write) and
            baseline["source_bindings"][source] != current["source_bindings"][source]
            for source in contract.sources):
            return "write_scope_violation"
        return "write_scope_violation" if reason in ("outside_scope_changed", "outside_scope_index_changed") else "authority_changed" if reason in ("source_changed", "git_authority_changed", "git_control_changed", "execution_or_contract_changed") else reason
    return None

def bind_commit(contract, input_path):
    """Trusted local observer must establish clean commit correspondence before CI use.

    Ignores no untracked/ignored relevant files. Dirty snapshots cannot inherit HEAD CI PASS.
    """
    from .._primitives import runner
    from ..domain.snapshot import CommitSnapshotBinding
    before,current=capture(contract,input_path)
    status=runner.git_read(snapshot.git_binary(),contract.repository,
        ["--no-pager","status","--porcelain=v1","-z","--untracked-files=all","--ignored"])
    if status:
        raise RuntimeError("ci_snapshot_not_commit_bound")
    after,_=capture(contract,input_path)
    if before!=after:
        raise RuntimeError("snapshot_race")
    return CommitSnapshotBinding(contract.repository,current.commit,current.digest)
