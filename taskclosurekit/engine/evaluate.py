"""Pure fail-closed evaluation; no IO, execution or automatic closure."""
from .freshness import freshness
from ..domain.contract import identity
from ..domain.claim import Claim
from ..domain.state import Evaluation
from ..trust.evidence_sources import valid_source
from ..trust.independence import independence

def evaluate(contract, current, evidence, review=None, *, authorized=False, baselined=False,
             authority_error=None, interrupted=False, known_state=True, closed=None):
    reasons = []
    fresh = []
    latest = {}
    for item in evidence:
        status = freshness(item, contract, current)
        fresh.append((item.id, status))
        if not valid_source(item.source, item.trust_class):
            reasons.append("invalid_evidence_source")
            continue
        if (item.type not in contract.authority.presets or item.result not in ("PASS", "FAIL") or
            any(c not in {c.id for c in contract.acceptance} for c in item.criteria)):
            reasons.append("invalid_evidence")
            continue
        if item.trust_class not in ("measured_local", "measured_ci"):
            continue
        if item.type not in latest or item.sequence > latest[item.type].sequence:
            latest[item.type] = item
    selected = tuple(sorted((item.id, item.sequence, item.result, item.contract_digest, item.snapshot.digest)
                            for item in latest.values()))
    evidence_set = identity(selected)
    if not known_state:
        reasons.append("unknown_state")
    if not authorized:
        reasons.append("task_authority_unconfirmed")
    if not baselined:
        reasons.append("missing_baseline")
    if authority_error:
        reasons.append(authority_error)
    if interrupted:
        reasons.append("interrupted_check")
    missing = []
    for criterion in contract.acceptance:
        good = []
        for ref in criterion.evidence:
            item = latest.get(ref)
            good.append(bool(item and item.result == "PASS" and criterion.id in item.criteria and
                             freshness(item, contract, current) == "CURRENT"))
        if criterion.required and not (all(good) if criterion.composition=="all_of" else any(good)):
            missing.append(criterion.id)
            refs = [latest.get(ref) for ref in criterion.evidence]
            reasons.append("required_evidence_failed" if any(x and x.result=="FAIL" for x in refs) else
                           "stale_evidence" if any(x and freshness(x,contract,current)!="CURRENT" for x in refs) else
                           "missing_required_evidence")
    independent = independence(contract.review_independence=="required")
    review_current = bool(review and review.contract_digest==contract.digest and review.snapshot_digest==current.digest and
                          review.evidence_set==evidence_set and review.verdict=="approve" and
                          valid_source(review.source, "human_confirmed") and review.independence==independent)
    if contract.review_required and not review_current:
        reasons.append("stale_review" if review and (review.snapshot_digest!=current.digest or review.evidence_set!=evidence_set or review.contract_digest!=contract.digest) else "missing_trusted_review")
    if contract.review_independence=="required" and independent!="PROVEN":
        reasons.append("independence_unknown")
    reasons = tuple(dict.fromkeys(reasons))
    candidate = Claim(contract.claim_type, contract.digest, current.digest, evidence_set,
                      tuple(c.id for c in contract.acceptance if c.required)) if not reasons else None
    if reasons:
        state = "INVALID" if any(x in reasons for x in ("unknown_state", "invalid_evidence", "invalid_evidence_source")) else "STALE" if "stale_evidence" in reasons or "stale_review" in reasons or authority_error else "BLOCKED"
        next_action = "new_task" if authority_error else "inspect_then_new_task" if interrupted else "authorize" if not authorized else "baseline" if not baselined else "trusted_independence_adapter_required" if "independence_unknown" in reasons else "check" if missing else "review"
    else:
        state = "CLOSED" if closed and closed.claim==candidate else "CLAIMABLE"
        next_action = "none" if state=="CLOSED" else "close"
    return Evaluation(state, "CLAIMABLE" if not reasons else "NOT_CLAIMABLE", reasons, tuple(fresh),
                      evidence_set, independent, candidate, next_action)
