"""Repository adapter enforces strict inventory/Git/path/bounded IO protections."""
from .._primitives import policy, snapshot
from ..domain.contract import identity
from ..domain.snapshot import Snapshot
from ..execution.presets import REGISTRY, observe, validate_record

PROGRAM_ROOTS = ("taskclosurekit", "tests")

def equivalent_capture(before,after):
    # Live leases are reverified on each capture. Their timestamps do not change
    # bound material bytes; every other raw control/input must remain identical.
    return {k:v for k,v in before.items() if k!="dependency_proofs"}=={k:v for k,v in after.items() if k!="dependency_proofs"}

def capture(contract, input_path, registry_record=None, integration_record=None, dependency_provider=None):
    from ..integration.dependencies import protected_capture
    with protected_capture(integration_record,contract,dependency_provider) as guard:
        return guarded_capture(contract,input_path,registry_record,integration_record,guard)

def guarded_capture(contract,input_path,registry_record,integration_record,guard):
    cached,proofs={},None
    if integration_record:
        from ..integration.dependencies import observe as observe_dependencies
        cached,proofs=observe_dependencies(integration_record,contract,guard)
    raw = snapshot.capture(contract.repository, input_path, contract.sources, program_roots=PROGRAM_ROOTS,
        capture_budget=integration_record["policy"].get("capture_budget") if integration_record else None,immutable_entries=cached)
    if proofs:
        from ..integration.dependencies import validate_observation as validate_dependencies
        import time
        validate_dependencies(proofs,integration_record,contract,time.time_ns())
        raw["dependency_proofs"]=proofs
    if registry_record is not None:
        raw["capabilities"] = observe(registry_record, contract, raw["entries"])
    if integration_record is not None:
        from ..integration.policy import observe as observe_integration
        raw["integration"] = observe_integration(integration_record)
    # Includes current physical root/provenance and live guarded host state,
    # not merely the proof saved before reading the repository.
    guard.validate()
    return raw, describe(raw, contract, registry_record, integration_record)

def describe(raw, contract, registry_record=None, integration_record=None):
    digest = snapshot.digest(raw)
    bounded = {key: value for key, value in raw.items() if key not in ("capabilities", "integration","dependency_proofs")}
    policy.valid_snapshot({"snapshot":bounded, "digest":snapshot.digest(bounded)}, contract.snapshot_policy())
    registry = validate_record(registry_record, contract) if registry_record else REGISTRY
    capability = raw.get("capabilities")
    if registry_record:
        from ..domain.contract import fields
        from ..execution.presets import validate_binding
        fields(capability, ("registry_digest", "config_binding", "authority_bindings", "runtime_bindings", *(("path_bindings","dispatcher_bindings") if registry_record.get("schema") == "taskclosurekit/presets/v2" else ())))
        if (capability["registry_digest"] != identity(registry_record) or
                set(capability["authority_bindings"]) != set(registry_record["authority_bindings"]) or
                set(capability["runtime_bindings"]) != set(registry_record["runtime_bindings"])):
            raise RuntimeError("invalid_preset_binding")
        for binding, resource in [(capability["config_binding"], False),
                *((v, False) for v in capability["authority_bindings"].values()),
                *((v, True) for v in capability["runtime_bindings"].values())]:
            if not (type(binding) is dict and set(binding) == {"missing"} and binding["missing"] is True):
                validate_binding(binding, resource=resource)
        if registry_record.get("schema") == "taskclosurekit/presets/v2":
            from ..execution.presets import validate_path_binding, validate_dispatcher_binding
            if capability["path_bindings"] != {"unknown":True}:
                validate_path_binding(capability["path_bindings"], [registry.get(p) for p in contract.authority.presets if p != "git-index-whitespace-v1"], capability["runtime_bindings"])
            dispatch = capability["dispatcher_bindings"]
            if dispatch != {"unknown":True}:
                if type(dispatch) is not dict or set(dispatch) != set(registry_record["dispatcher_bindings"]):
                    raise RuntimeError("invalid_dispatcher_binding")
                for binding in dispatch.values():
                    validate_dispatcher_binding(binding)
    elif capability is not None:
        raise RuntimeError("invalid_preset_binding")
    if integration_record:
        from ..integration.policy import validate_record_config, validate_observation
        validate_record_config(integration_record, contract, registry_record)
        validate_observation(raw.get("integration"), integration_record)
        if raw.get("capture_budget")!=integration_record["policy"].get("capture_budget"):
            raise RuntimeError("invalid_capture_budget")
        from ..integration.dependencies import validate_observation as validate_dependencies
        validate_dependencies(raw.get("dependency_proofs"),integration_record,contract,entries=raw["entries"])
    elif "integration" in raw:
        raise RuntimeError("invalid_integration_binding")
    program = raw["execution"]["program"]
    if not all(any(p.startswith(root+"/") for p in program) for root in PROGRAM_ROOTS):
        raise RuntimeError("incomplete_program_identity")
    repository_identity = identity({"entries":raw["entries"],"controls":raw["controls"]})
    if integration_record:
        from ..integration.policy import semantic_repository
        repository_identity = identity(semantic_repository(raw, integration_record))
        digest = identity({"repository":repository_identity,"index_content":raw["controls"]["index_entries"],
            "execution":raw["execution"],"contract_input":raw["contract_input"],"sources":raw["source_bindings"],"capabilities":capability,"integration":raw["integration"]})
    return Snapshot(digest, repository_identity,
        identity({"contract":contract.digest,"input":raw["contract_input"],"sources":raw["source_bindings"], **({"capabilities":{k:v for k,v in capability.items() if k not in ("runtime_bindings", "path_bindings")}} if capability else {}), **({"integration":raw["integration"]} if integration_record else {})}),
        identity({"execution":raw["execution"],"presets":[registry.get(p).digest for p in contract.authority.presets], **({"runtime":capability["runtime_bindings"], **({"path_bindings":capability["path_bindings"]} if "path_bindings" in capability else {})} if capability else {})}),
        raw["controls"]["head"])

def preset_error(current, registry_record):
    if not registry_record:
        return None
    capability = current["capabilities"]
    if (capability["config_binding"] != registry_record["config_binding"] or
            capability["authority_bindings"] != registry_record["authority_bindings"] or
            capability.get("dispatcher_bindings") != registry_record.get("dispatcher_bindings")):
        return "preset_authority_changed"
    if capability["runtime_bindings"] != registry_record["runtime_bindings"] or capability.get("path_bindings") != registry_record.get("path_bindings"):
        return "preset_environment_changed"
    return None


def authority_error(contract, baseline, current, expected_input, registry_record=None, integration_record=None):
    error = preset_error(current, registry_record)
    if error:
        return error
    if current["contract_input"] != expected_input:
        return "authority_changed"
    if integration_record:
        from ..integration.policy import permitted_change
        return permitted_change(contract, baseline, current, integration_record)
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


def result_identity(contract, current, integration_record=None):
    """Read-only stage projection. It grants no remote, CI or deployment claim."""
    from .._primitives import runner
    git = snapshot.git_binary()
    before = runner.git_read(git, contract.repository, ["rev-parse", "--verify", "HEAD"])
    try:
        staged = runner.git_read(git, contract.repository,
            ["--no-pager", "diff", "--cached", "--name-only", "-z", "--no-ext-diff", "--no-textconv", "HEAD", "--"])
        unstaged = runner.git_read(git, contract.repository,
            ["--no-pager", "diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv", "--"])
        untracked = runner.git_read(git, contract.repository,
            ["ls-files", "--others", "-z", "--"])
    except RuntimeError as error:
        if str(error) != "git_snapshot_incomplete":
            raise
        return {"stage":"unknown", "commit":current.commit, "snapshot":current.digest,
            "local_ready":False, "remote_verified":False, "ci_verified":False, "deployed":False,
            "limitation":"stage_inspection_unavailable"}
    if integration_record:
        from ..integration.policy import excluded
        untracked = b"\0".join(name for name in untracked.split(b"\0")
            if name and not excluded(name.decode("utf-8", "strict"), integration_record))
    after = runner.git_read(git, contract.repository, ["rev-parse", "--verify", "HEAD"])
    if before != after or after.decode("ascii").strip() != current.commit:
        raise RuntimeError("snapshot_race")
    stage = "precommit" if unstaged or untracked else "staged-review" if staged else "committed"
    return {"stage":stage, "commit":current.commit, "snapshot":current.digest,
            "local_ready":False, "remote_verified":False, "ci_verified":False, "deployed":False}
