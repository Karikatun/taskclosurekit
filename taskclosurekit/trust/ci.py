"""Future external evidence port. No network/provider implementation is included."""
from dataclasses import dataclass
from ..domain.evidence import Evidence, EvidenceSource
from ..domain.contract import identity, identifier
from ..domain.snapshot import CommitSnapshotBinding
import re

def hash_string(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}",value):
        raise RuntimeError("invalid_digest")
    return value

@dataclass(frozen=True)
class VerifiedCIRuntime:
    run_id: str
    inputs_digest: str
    execution_digest: str
    verified_ns: int
    source_authenticated: bool
    runtime_verified: bool

@dataclass(frozen=True)
class VerifiedCIResult:
    repository: str
    commit: str
    workflow_identity: str
    selected_check: str
    preset_id: str
    criteria: tuple
    result: str
    artifact_digest: str
    runtime_proof: object = None

class CIAdapter:
    """Trusted host configuration, never instantiated from contract/imported JSON.

    Implementations must verify provider authenticity before returning VerifiedCIResult.
    The local interface checks exact bindings; it cannot establish provider truth itself.
    """
    trusted_workflow_identity = None
    adapter_identity = None
    selected_checks = None

    def verify(self, selector):
        raise NotImplementedError

def accept_ci(adapter, selector, contract, current, sequence, recorded_ns, *, commit_binding=None):
    if commit_binding != CommitSnapshotBinding(contract.repository,current.commit,current.digest):
        raise RuntimeError("ci_snapshot_not_commit_bound")
    if not isinstance(adapter,CIAdapter):
        raise RuntimeError("untrusted_ci_adapter")
    hash_string(adapter.trusted_workflow_identity);hash_string(adapter.adapter_identity)
    if type(adapter.selected_checks) is not dict or not adapter.selected_checks:
        raise RuntimeError("untrusted_ci_adapter")
    verified=adapter.verify(selector)
    if not isinstance(verified,VerifiedCIResult):
        raise RuntimeError("unverified_ci_evidence")
    runtime=verified.runtime_proof
    if (not isinstance(runtime,VerifiedCIRuntime) or type(runtime.run_id) is not str or
        not 1<=len(runtime.run_id)<=128 or any(ord(c)<32 for c in runtime.run_id) or
        runtime.inputs_digest!=current.digest or runtime.execution_digest!=current.execution or
        type(runtime.verified_ns) is not int or type(recorded_ns) is not int or
        not 0<runtime.verified_ns<=recorded_ns or recorded_ns-runtime.verified_ns>600*10**9 or
        runtime.source_authenticated is not True or runtime.runtime_verified is not True):
        raise RuntimeError("unverified_ci_runtime")
    expected=tuple(c.id for c in contract.acceptance if verified.preset_id in c.evidence)
    if (verified.repository!=contract.repository or verified.commit!=current.commit or
        verified.workflow_identity!=adapter.trusted_workflow_identity or
        adapter.selected_checks.get(verified.selected_check)!=verified.preset_id or
        verified.preset_id not in contract.authority.presets or verified.criteria!=expected or
        verified.result!="PASS"):
        raise RuntimeError("ci_binding_mismatch")
    hash_string(verified.artifact_digest);identifier(verified.selected_check)
    if type(sequence) is not int or sequence<=0 or type(recorded_ns) is not int or recorded_ns<=0:
        raise RuntimeError("invalid_sequence_or_time")
    verification={"repository":verified.repository,"commit":verified.commit,
        "workflow_identity":verified.workflow_identity,"selected_check":verified.selected_check,
        "preset_id":verified.preset_id,"criteria":list(verified.criteria),
        "adapter_identity":adapter.adapter_identity,"artifact_digest":verified.artifact_digest}
    verification["runtime"]={"run_id":runtime.run_id,"inputs_digest":runtime.inputs_digest,
        "execution_digest":runtime.execution_digest,"verified_ns":runtime.verified_ns,
        "source_authenticated":True,"runtime_verified":True}
    evidence=Evidence("ci-"+str(sequence),verified.preset_id,"measured_ci",
        EvidenceSource("ci-adapter","measured_ci"),contract.digest,current,expected,"PASS",sequence,recorded_ns)
    return evidence,verification
