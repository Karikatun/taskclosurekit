"""Strict semantic validation of HMAC-verified events; unknown state is invalid."""
from dataclasses import asdict, dataclass
from taskproof import core, runner, snapshot
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


def hash_string(value):
    if not core.hash_string(value):
        raise RuntimeError("invalid_digest")
    return value

def positive(value):
    if type(value) is not int or value <= 0:
        raise RuntimeError("invalid_sequence_or_time")
    return value

def confirmation(value, binding):
    validate_confirmation(value, binding)

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
    if value["verdict"] not in ("approve", "reject") or value["independence"] not in ("UNKNOWN", "NOT_REQUIRED"):
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


def replay(events):
    initial = events[0]
    if initial["kind"]!="CONTRACT_CREATED":
        raise RuntimeError("invalid_initial_state")
    payload=initial["payload"]
    fields(payload,("contract", "input_path", "input_hash"))
    contract=parse_contract(payload["contract"])
    if contract.id != initial["run_id"]:
        raise RuntimeError("store_binding_mismatch")
    if type(payload["input_path"]) is not str or not payload["input_path"].startswith("/"):
        raise RuntimeError("invalid_contract_input_path")
    core.valid_entry(payload["input_hash"])
    if payload["input_hash"]["kind"]!="file":
        raise RuntimeError("invalid_contract_input_path")
    tx=Transaction(contract,payload["input_path"],payload["input_hash"])
    for event in events[1:]:
        kind,p=event["kind"],event["payload"]
        if tx.closure or (tx.started and kind!="CHECK_COMPLETED"):
            raise RuntimeError("invalid_state_sequence")
        if kind=="AUTHORITY_VALIDATED" and tx.state=="DRAFT":
            fields(p,("confirmation",))
            confirmation(p["confirmation"], action_binding("authorize",contract.digest,None,None))
            tx.authorized=True;tx.state="AUTHORIZED"
        elif kind=="BASELINE_CAPTURED" and tx.state=="AUTHORIZED":
            fields(p,("snapshot", "identity"))
            model=describe(p["snapshot"],contract)
            if asdict(model)!=p["identity"] or p["snapshot"]["contract_input"] != tx.input_hash:
                raise RuntimeError("snapshot_binding_mismatch")
            tx.baseline=p["snapshot"];tx.state="BASELINED"
        elif kind=="CHECK_STARTED" and tx.baseline:
            fields(p,("snapshot", "identity", "preset_id", "started_ns"))
            model=describe(p["snapshot"],contract)
            if p["identity"]!=asdict(model) or p["preset_id"] not in contract.authority.presets or authority_error(contract,tx.baseline,p["snapshot"],tx.input_hash):
                raise RuntimeError("execution_or_contract_changed")
            positive(p["started_ns"])
            tx.started=p;tx.state="ACTIVE"
        elif kind=="CHECK_COMPLETED" and tx.started:
            fields(p,("evidence", "journal", "reason"))
            item=evidence_from(p["evidence"])
            started=tx.started
            fields(p["journal"], ("kind", "stdout_bytes", "stderr_bytes", "exit_code", "timeout", "truncated", "duration_ms"))
            j=p["journal"]
            if any(type(j[x]) is not int for x in ("stdout_bytes","stderr_bytes","exit_code","duration_ms")) or any(j[x]<0 for x in ("stdout_bytes","stderr_bytes","duration_ms")) or type(j["timeout"]) is not bool or type(j["truncated"]) is not bool or j["kind"]!="discarded-output-v1" or not -64<=j["exit_code"]<=255:
                raise RuntimeError("invalid_execution_journal")
            output=j["stdout_bytes"]+j["stderr_bytes"]
            if output>runner.OUTPUT_LIMIT+8192 or (not j["truncated"] and output>runner.OUTPUT_LIMIT):
                raise RuntimeError("invalid_execution_journal")
            expected_reason="check_timeout" if j["timeout"] else "check_output_limit" if j["truncated"] else "check_failed" if j["exit_code"]!=0 else None
            if p["reason"] != expected_reason and not (expected_reason is None and p["reason"]=="inputs_changed_during_check"):
                raise RuntimeError("invalid_check_reason")
            criteria=tuple(c.id for c in contract.acceptance if started["preset_id"] in c.evidence)
            if (item.id!="check-"+str(event["seq"]) or item.sequence!=event["seq"] or item.recorded_ns<started["started_ns"] or
                item.type!=started["preset_id"] or item.trust_class!="measured_local" or item.source!=EvidenceSource("local-preset","measured_local") or
                item.contract_digest!=contract.digest or asdict(item.snapshot)!=started["identity"] or item.criteria!=criteria or
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
            fields(p,("review", "confirmation"))
            review=review_from(p["review"])
            latest=tx.evidence[-1].snapshot
            assessed=evaluate(contract,latest,tx.evidence,authorized=tx.authorized,baselined=True)
            if review.sequence!=event["seq"] or review.contract_digest!=contract.digest or review.snapshot_digest!=latest.digest or review.evidence_set!=assessed.evidence_set:
                raise RuntimeError("review_binding_mismatch")
            if review.source==EvidenceSource("local-operator","human_confirmed"):
                confirmation(p["confirmation"],action_binding("review",contract.digest,latest.digest,assessed.evidence_set))
            elif review.source==EvidenceSource("agent-import","agent_attested"):
                if p["confirmation"] is not None:
                    raise RuntimeError("invalid_review_source")
            else:
                raise RuntimeError("invalid_review_source")
            expected="UNKNOWN" if contract.review_independence=="required" else "NOT_REQUIRED"
            if review.independence!=expected:
                raise RuntimeError("independence_unknown")
            tx.review=review;tx.state="REVIEWED"
        elif kind=="CLAIM_CLOSED" and tx.baseline and tx.evidence:
            fields(p,("closure", "confirmation"))
            c=p["closure"]
            fields(c,("claim", "authority_source", "action_binding", "sequence", "identity_verified"))
            positive(c["sequence"])
            claim=claim_from(c["claim"])
            latest=tx.evidence[-1].snapshot
            assessed=evaluate(contract,latest,tx.evidence,tx.review,authorized=tx.authorized,baselined=True)
            expected=action_binding("close",contract.digest,latest.digest,assessed.evidence_set)
            if assessed.decision!="CLAIMABLE" or claim!=assessed.claim or c["action_binding"]!=expected or c["authority_source"]!="local-operator" or c["sequence"]!=event["seq"] or c["identity_verified"] is not False:
                raise RuntimeError("close_binding_mismatch")
            confirmation(p["confirmation"],expected)
            tx.closure=Closure(claim,c["authority_source"],expected,c["sequence"]);tx.state="CLOSED"
        else:
            raise RuntimeError("invalid_state_sequence")
        if not tx.started:
            tx.last_confirmed=tx.state
    return tx
