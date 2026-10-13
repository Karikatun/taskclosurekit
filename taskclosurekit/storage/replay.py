"""Strict semantic validation of HMAC-verified events; unknown state is invalid."""
from dataclasses import asdict, dataclass
from .._primitives import policy, runner, snapshot
from ..domain.contract import fields, identity, parse_contract
from ..domain.evidence import Evidence, EvidenceSource
from ..domain.review import Review
from ..domain.assertion import AgentAssertion
from ..domain.claim import Claim, Closure, LIMITATIONS
from ..domain.snapshot import Snapshot
from ..engine.closure import action_binding
from ..engine.evaluate import evaluate
from ..trust.confirmation import validate_confirmation
from ..snapshots.repository import describe, authority_error
from ..execution.presets import REGISTRY, validate_record
from ..integration.policy import task_authority_digest as authority_digest, validate_record_config
from ..execution.runner import permitted_outputs


def hash_string(value):
    if not policy.hash_string(value):
        raise RuntimeError("invalid_digest")
    return value

def positive(value):
    if type(value) is not int or value <= 0:
        raise RuntimeError("invalid_sequence_or_time")
    return value

def confirmation(value, binding):
    validate_confirmation(value, binding)

def authorization(value, contract, operation, binding):
    from ..integration.adapters import capability_from_record, authority_request, selector_value
    fields(value,("capability","selector","provider_identity","recorded_ns"))
    hash_string(value["provider_identity"]);positive(value["recorded_ns"]);selector_value(value["selector"])
    return capability_from_record(value["capability"],authority_request(contract,operation,binding),value["recorded_ns"])

def evidence_from(value):
    fields(value, ("id", "type", "trust_class", "source", "contract_digest", "snapshot", "criteria", "result", "sequence", "recorded_ns"))
    fields(value["source"], ("id", "trust_class"))
    fields(value["snapshot"], ("digest", "repository", "authority", "execution", "commit"))
    for name in ("digest", "repository", "authority", "execution"):
        hash_string(value["snapshot"][name])
    if type(value["snapshot"]["commit"]) is not str or len(value["snapshot"]["commit"]) != 40 or any(x not in "0123456789abcdef" for x in value["snapshot"]["commit"]):
        raise RuntimeError("invalid_git_identity")
    if type(value["criteria"]) is not list or not 1 <= len(value["criteria"]) <= 100 or any(type(c) is not str for c in value["criteria"]) or len(set(value["criteria"])) != len(value["criteria"]):
        raise RuntimeError("invalid_evidence_criteria")
    if type(value["id"]) is not str or len(value["id"]) > 96 or value["result"] not in ("PASS", "FAIL"):
        raise RuntimeError("invalid_evidence")
    positive(value["sequence"]);positive(value["recorded_ns"]);hash_string(value["contract_digest"])
    return Evidence(value["id"],value["type"],value["trust_class"],EvidenceSource(**value["source"]),
                    value["contract_digest"],Snapshot(**value["snapshot"]),tuple(value["criteria"]),
                    value["result"],value["sequence"],value["recorded_ns"])

def review_from(value):
    fields(value, ("contract_digest", "snapshot_digest", "evidence_set", "verdict", "source", "independence", "sequence"))
    for key in ("contract_digest", "snapshot_digest", "evidence_set"):
        hash_string(value[key])
    fields(value["source"], ("id", "trust_class"))
    if value["verdict"] not in ("approve", "reject") or value["independence"] not in ("UNKNOWN", "NOT_REQUIRED", "PROVEN"):
        raise RuntimeError("invalid_review")
    positive(value["sequence"])
    return Review(value["contract_digest"],value["snapshot_digest"],value["evidence_set"],value["verdict"],
                  EvidenceSource(**value["source"]),value["independence"],value["sequence"])

def claim_from(value):
    fields(value, ("type", "contract_digest", "snapshot_digest", "evidence_set", "criteria", "limitations"))
    if type(value["criteria"]) is not list or tuple(value["limitations"])!=LIMITATIONS:
        raise RuntimeError("invalid_claim")
    for key in ("contract_digest", "snapshot_digest", "evidence_set"):
        hash_string(value[key])
    return Claim(value["type"],value["contract_digest"],value["snapshot_digest"],value["evidence_set"],tuple(value["criteria"]),tuple(value["limitations"]))

@dataclass
class Transaction:
    contract: object
    input_path: str
    input_hash: dict
    state: str = "DRAFT"
    authorized: bool = False
    baseline: object = None
    evidence: tuple = ()
    assertions: tuple = ()
    review: object = None
    closure: object = None
    started: object = None
    last_confirmed: str = "DRAFT"
    registry_record: object = None
    integration_record: object = None
    host_review: object = None
    delegated_authority: object = None
    close_attempt: object = None


def replay(events):
    initial = events[0]
    if initial["kind"]!="CONTRACT_CREATED":
        raise RuntimeError("invalid_initial_state")
    payload=initial["payload"]
    fields(payload,("contract", "input_path", "input_hash", *(("registry",) if "registry" in payload else ()), *(("integration",) if "integration" in payload else ())))
    contract=parse_contract(payload["contract"])
    if contract.id != initial["run_id"]:
        raise RuntimeError("store_binding_mismatch")
    if type(payload["input_path"]) is not str or not payload["input_path"].startswith("/"):
        raise RuntimeError("invalid_contract_input_path")
    policy.valid_entry(payload["input_hash"])
    if payload["input_hash"]["kind"]!="file":
        raise RuntimeError("invalid_contract_input_path")
    record = payload.get("registry")
    if "registry" in payload and type(record) is not dict:
        raise RuntimeError("invalid_preset_configuration")
    registry = validate_record(record, contract) if record else REGISTRY
    for preset_id in contract.authority.presets:
        registry.get(preset_id)
    integration_record = payload.get("integration")
    if integration_record is not None:
        validate_record_config(integration_record, contract, record)
    tx=Transaction(contract,payload["input_path"],payload["input_hash"],registry_record=record,integration_record=integration_record)
    for event in events[1:]:
        kind,p=event["kind"],event["payload"]
        if tx.closure or (tx.started and kind!="CHECK_COMPLETED"):
            raise RuntimeError("invalid_state_sequence")
        if tx.close_attempt and kind not in ("CLOSE_CAPABILITY_CONSUMED","CLOSE_CAPABILITY_REFUSED","CLAIM_CLOSED"):
            raise RuntimeError("capability_attempt_requires_recovery")
        if kind=="AUTHORITY_VALIDATED" and tx.state=="DRAFT":
            delegated="authorization" in p
            fields(p,("authorization",) if delegated else ("confirmation",))
            binding=action_binding("authorize",authority_digest(contract.digest,record,integration_record),None,None)
            if delegated:
                authorization(p["authorization"],contract,"authorize",binding)
                tx.delegated_authority=p["authorization"]
            else:
                confirmation(p["confirmation"], binding)
            tx.authorized=True;tx.state="AUTHORIZED"
        elif kind=="BASELINE_CAPTURED" and tx.state=="AUTHORIZED":
            fields(p,("snapshot", "identity"))
            model=describe(p["snapshot"],contract,record,integration_record)
            if asdict(model)!=p["identity"] or p["snapshot"]["contract_input"] != tx.input_hash:
                raise RuntimeError("snapshot_binding_mismatch")
            tx.baseline=p["snapshot"];tx.state="BASELINED"
        elif kind=="CHECK_STARTED" and tx.baseline:
            fields(p,("snapshot", "identity", "preset_id", "started_ns"))
            model=describe(p["snapshot"],contract,record,integration_record)
            if p["identity"]!=asdict(model) or p["preset_id"] not in contract.authority.presets or authority_error(contract,tx.baseline,p["snapshot"],tx.input_hash,record,integration_record):
                raise RuntimeError("execution_or_contract_changed")
            positive(p["started_ns"])
            tx.started=p;tx.state="ACTIVE"
        elif kind=="CHECK_COMPLETED" and tx.started:
            fields(p,("evidence", "journal", "reason", *(("snapshot", "identity") if record else ())))
            item=evidence_from(p["evidence"])
            started=tx.started
            j=p["journal"]
            preset = registry.get(started["preset_id"])
            from ..execution.runner import validate_result, result_reason
            validate_result(j, preset)
            expected_reason = result_reason(j, preset)
            if p["reason"] != expected_reason and not (expected_reason is None and p["reason"]=="inputs_changed_during_check"):
                raise RuntimeError("invalid_check_reason")
            measured_identity = started["identity"]
            if record:
                after_model = describe(p["snapshot"],contract,record,integration_record)
                if asdict(after_model) != p["identity"]:
                    raise RuntimeError("snapshot_binding_mismatch")
                unchanged = permitted_outputs(started["snapshot"],p["snapshot"],preset)
                if p["reason"] is None and (not unchanged or authority_error(contract,tx.baseline,p["snapshot"],tx.input_hash,record,integration_record)):
                    raise RuntimeError("invalid_success_receipt")
                if expected_reason is None and p["reason"] == "inputs_changed_during_check" and unchanged:
                    raise RuntimeError("invalid_check_reason")
                if p["reason"] is None:
                    measured_identity = p["identity"]
            criteria=tuple(c.id for c in contract.acceptance if started["preset_id"] in c.evidence)
            if (item.id!="check-"+str(event["seq"]) or item.sequence!=event["seq"] or item.recorded_ns<started["started_ns"] or
                item.type!=started["preset_id"] or item.trust_class!="measured_local" or item.source!=EvidenceSource("local-preset","measured_local") or
                item.contract_digest!=contract.digest or asdict(item.snapshot)!=measured_identity or item.criteria!=criteria or
                item.result!=("FAIL" if p["reason"] else "PASS")):
                raise RuntimeError("receipt_binding_mismatch")
            tx.evidence=(*tx.evidence,item);tx.started=None;tx.state="EVIDENCED"
        elif kind=="AGENT_ASSERTION_RECORDED" and tx.baseline:
            fields(p,("assertion",))
            a=p["assertion"]
            fields(a,("id","statement_digest","source","contract_digest","snapshot_digest","sequence","recorded_ns"))
            fields(a["source"],("id","trust_class"))
            positive(a["sequence"])
            for name in ("statement_digest","contract_digest","snapshot_digest"):
                hash_string(a[name])
            if (a["source"]!={"id":"agent-import","trust_class":"agent_attested"} or
                a["contract_digest"]!=contract.digest or a["sequence"]!=event["seq"] or a["id"]!="assertion-"+str(event["seq"])):
                raise RuntimeError("invalid_assertion_source")
            positive(a["recorded_ns"])
            tx.assertions=(*tx.assertions,AgentAssertion(a["id"],a["statement_digest"],EvidenceSource(**a["source"]),
                a["contract_digest"],a["snapshot_digest"],a["sequence"],a["recorded_ns"]))
        elif kind=="REVIEW_RECORDED" and tx.baseline and tx.evidence:
            hosted = "host_proof" in p
            fields(p,("review", "confirmation", *(("host_proof","selector","provider_identity") if hosted else ()), *(("snapshot","identity") if integration_record else ())))
            review=review_from(p["review"])
            latest=tx.evidence[-1].snapshot
            if integration_record:
                latest=describe(p["snapshot"],contract,record,integration_record)
                if asdict(latest)!=p["identity"] or authority_error(contract,tx.baseline,p["snapshot"],tx.input_hash,record,integration_record):
                    raise RuntimeError("review_binding_mismatch")
            assessed=evaluate(contract,latest,tx.evidence,authorized=tx.authorized,baselined=True)
            if review.sequence!=event["seq"] or review.contract_digest!=contract.digest or review.snapshot_digest!=latest.digest or review.evidence_set!=assessed.evidence_set:
                raise RuntimeError("review_binding_mismatch")
            if review.source==EvidenceSource("local-operator","human_confirmed"):
                confirmation(p["confirmation"],action_binding("review",contract.digest,latest.digest,assessed.evidence_set))
            elif review.source==EvidenceSource("agent-import","agent_attested"):
                if p["confirmation"] is not None:
                    raise RuntimeError("invalid_review_source")
            elif hosted and review.source==EvidenceSource("host-review","host_verified"):
                from ..integration.adapters import review_from_record, review_request, selector_value
                proof = review_from_record(p["host_proof"])
                selector_value(p["selector"]);hash_string(p["provider_identity"])
                if proof.request != review_request(contract,latest,assessed.evidence_set) or proof.verdict!=review.verdict or p["confirmation"] is not None:
                    raise RuntimeError("host_review_binding_mismatch")
                tx.host_review = {"proof":proof,"selector":p["selector"],"provider_identity":p["provider_identity"]}
            else:
                raise RuntimeError("invalid_review_source")
            expected="PROVEN" if hosted else "UNKNOWN" if contract.review_independence=="required" else "NOT_REQUIRED"
            if review.independence!=expected:
                raise RuntimeError("independence_unknown")
            if not hosted:
                tx.host_review = None
            tx.review=review;tx.state="REVIEWED"
        elif kind=="CLOSE_CAPABILITY_STARTED" and tx.baseline and tx.evidence:
            fields(p,("authorization","snapshot","identity"))
            model=describe(p["snapshot"],contract,record,integration_record)
            assessed=evaluate(contract,model,tx.evidence,tx.review,authorized=tx.authorized,baselined=True,
                authority_error=authority_error(contract,tx.baseline,p["snapshot"],tx.input_hash,record,integration_record))
            binding=action_binding("close",contract.digest,model.digest,assessed.evidence_set)
            if tx.close_attempt or asdict(model)!=p["identity"] or assessed.decision!="CLAIMABLE":
                raise RuntimeError("close_binding_mismatch")
            authorization(p["authorization"],contract,"close",binding)
            tx.close_attempt={"authorization":p["authorization"],"status":"started","binding":binding}
        elif kind=="CLOSE_CAPABILITY_CONSUMED" and tx.close_attempt:
            fields(p,("binding",))
            if tx.close_attempt["status"]!="started" or p["binding"]!=tx.close_attempt["binding"]:
                raise RuntimeError("invalid_capability_audit")
            tx.close_attempt["status"]="consumed"
        elif kind=="CLOSE_CAPABILITY_REFUSED" and tx.close_attempt:
            fields(p,("binding","reason"))
            if tx.close_attempt["status"]!="consumed" or p["binding"]!=tx.close_attempt["binding"] or p["reason"] not in ("inputs_changed_after_authority_callback","capability_expired_after_authority_callback"):
                raise RuntimeError("invalid_capability_audit")
            tx.close_attempt["status"]="refused"
        elif kind=="CLAIM_CLOSED" and tx.baseline and tx.evidence:
            delegated="authorization" in p
            fields(p,(*(("closure", "authorization", "consume_audit") if delegated else ("closure","confirmation")), *(("snapshot","identity") if integration_record else ())))
            c=p["closure"]
            fields(c,("claim", "authority_source", "action_binding", "sequence", "identity_verified"))
            positive(c["sequence"])
            claim=claim_from(c["claim"])
            latest=tx.evidence[-1].snapshot
            if integration_record:
                latest=describe(p["snapshot"],contract,record,integration_record)
                if asdict(latest)!=p["identity"] or authority_error(contract,tx.baseline,p["snapshot"],tx.input_hash,record,integration_record):
                    raise RuntimeError("close_binding_mismatch")
            assessed=evaluate(contract,latest,tx.evidence,tx.review,authorized=tx.authorized,baselined=True)
            expected=action_binding("close",contract.digest,latest.digest,assessed.evidence_set)
            if assessed.decision!="CLAIMABLE" or claim!=assessed.claim or c["action_binding"]!=expected or c["authority_source"]!=("delegated-authority" if delegated else "local-operator") or c["sequence"]!=event["seq"] or c["identity_verified"] is not delegated:
                raise RuntimeError("close_binding_mismatch")
            if delegated:
                # Delegated close was introduced with the durable one-time
                # audit. There is no unaudited historical delegated variant.
                if (p["consume_audit"] is not True or not tx.close_attempt or tx.close_attempt["status"]!="consumed" or tx.close_attempt["authorization"]!=p["authorization"]):
                    raise RuntimeError("invalid_capability_audit")
                capability=authorization(p["authorization"],contract,"close",expected)
                if tx.delegated_authority and capability.nonce==tx.delegated_authority["capability"]["nonce"]:
                    raise RuntimeError("capability_replayed")
            else:
                confirmation(p["confirmation"],expected)
            tx.closure=Closure(claim,c["authority_source"],expected,c["sequence"],delegated);tx.state="CLOSED";tx.close_attempt=None
        else:
            raise RuntimeError("invalid_state_sequence")
        if not tx.started:
            tx.last_confirmed=tx.state
    return tx
