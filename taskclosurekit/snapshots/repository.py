"""Adapter preserves strict v1 inventory/Git/path/bounded IO protections."""
from taskproof import core, snapshot
from ..domain.contract import identity
from ..domain.snapshot import Snapshot
from ..execution.presets import REGISTRY

PROGRAM_ROOTS = ("taskproof", "taskclosurekit", "tests")

def capture(contract, input_path):
    raw = snapshot.capture(contract.repository, input_path, contract.sources, program_roots=PROGRAM_ROOTS)
    return raw, describe(raw, contract)

def describe(raw, contract):
    digest = snapshot.digest(raw)
    core.valid_snapshot({"snapshot":raw, "digest":digest}, contract.legacy_policy())
    program = raw["execution"]["program"]
    if not all(any(p.startswith(root+"/") for p in program) for root in PROGRAM_ROOTS):
        raise RuntimeError("incomplete_program_identity")
    return Snapshot(digest, identity({"entries":raw["entries"],"controls":raw["controls"]}),
        identity({"contract":contract.digest,"input":raw["contract_input"],"sources":raw["source_bindings"]}),
        identity({"execution":raw["execution"],"presets":[REGISTRY.get(p).digest for p in contract.authority.presets]}),
        raw["controls"]["head"])

def authority_error(contract, baseline, current, expected_input):
    if current["contract_input"] != expected_input:
        return "authority_changed"
    try:
        core.permitted_change(contract.legacy_policy(), {"snapshot":baseline}, current)
    except RuntimeError as error:
        reason = str(error)
        if reason == "source_changed" and any(
            not core.scope_contains(source, contract.authority.write) and
            baseline["source_bindings"][source] != current["source_bindings"][source]
            for source in contract.sources):
            return "write_scope_violation"
        return "write_scope_violation" if reason in ("outside_scope_changed", "outside_scope_index_changed") else "authority_changed" if reason in ("source_changed", "git_authority_changed", "git_control_changed", "execution_or_contract_changed") else reason
    return None

def bind_commit(contract, input_path):
    """Trusted local observer must establish clean commit correspondence before CI use.

    Ignores no untracked/ignored relevant files. Dirty snapshots cannot inherit HEAD CI PASS.
    """
    from taskproof import runner
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
