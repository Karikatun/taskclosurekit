from ..domain.claim import Closure
from ..domain.contract import identity
from ..trust.confirmation import validate_confirmation

def action_binding(operation, contract_digest, snapshot_digest, evidence_set):
    return identity({"operation": operation, "contract":contract_digest,
                     "snapshot":snapshot_digest, "evidence_set":evidence_set})

def close(evaluation, confirmation, sequence):
    if evaluation.decision != "CLAIMABLE" or not evaluation.claim:
        raise RuntimeError("claim_not_claimable")
    expected = action_binding("close", evaluation.claim.contract_digest,
                              evaluation.claim.snapshot_digest, evaluation.claim.evidence_set)
    try:
        validate_confirmation(confirmation, expected)
    except RuntimeError:
        raise RuntimeError("closure_authority_unconfirmed") from None
    return Closure(evaluation.claim, "local-operator", expected, sequence)
