"""Transaction application service; IO and trust adapters live outside domain."""
from dataclasses import asdict
import time
from ._primitives import snapshot
from ._primitives.store import bounded_json
from .domain.contract import fields, parse_contract, identity
from .domain.evidence import Evidence, EvidenceSource
from .domain.review import Review
from .domain.assertion import AgentAssertion
from .engine.evaluate import evaluate
from .engine.closure import action_binding, close
from .execution.runner import run, permitted_outputs
from .execution.presets import REGISTRY, prepare_config, validate_record, authority_digest
from .snapshots.repository import capture, authority_error, preset_error
from .storage.local_hmac import LocalHmacStore
from .storage.replay import replay
from .trust.local_operator import LocalOperator


def envelope(operation, *, task_id=None, state="INVALID", decision="NOT_EVALUATED", reasons=(), status="ok", **extra):
    return {"schema":"taskclosurekit/result/v2", "operation":operation,
            "operational":{"status":status}, "task_id":task_id, "state":state,
            "decision":decision, "reasons":list(reasons), "freshness":[], "claim":None,
            "next_action":"none", **extra}

def outside(path, repo, store, kind):
    path=snapshot.safe_path(path)
    if snapshot.path_within(path,repo):
        raise RuntimeError(kind+"_input_must_be_outside_repository")
    if snapshot.path_within(path,store.path):
        raise RuntimeError(kind+"_input_must_be_outside_store")
    return path

def create(store_path, input_path, *, preset_config=None):
    path=snapshot.safe_path(input_path)
    value=bounded_json(path)
    contract=parse_contract(value)
    repo=snapshot.safe_path(contract.repository)
    if not repo.is_dir():
        raise RuntimeError("repository_required")
    store=LocalHmacStore(store_path,repo)
    outside(path,repo,store,"contract")
    registry_record = prepare_config(preset_config, contract, store) if preset_config is not None else None
    if registry_record is None:
        for preset_id in contract.authority.presets:
            REGISTRY.get(preset_id)
    identity=snapshot.regular(path,snapshot.Budget(),65536)[0]
    # Re-read to prevent contract bytes and input identity disagreeing under a race.
    if bounded_json(path)!=value or snapshot.regular(path,snapshot.Budget(),65536)[0]!=identity:
        raise RuntimeError("contract_input_race")
    with store.locked(create=True):
        store.initialize()
        store.append([],"CONTRACT_CREATED",{"contract":value,"input_path":str(path),"input_hash":identity, **({"registry":registry_record} if registry_record else {})},contract.id)
    return envelope("task.create",task_id=contract.id,state="DRAFT",next_action="authorize",contract_digest=contract.digest, authority_digest=authority_digest(contract.digest,registry_record))

def project(operation, tx, current, evaluation):
    result=envelope(operation,task_id=tx.contract.id,state=evaluation.state,decision=evaluation.decision,
        reasons=evaluation.reasons, freshness=[{"id":x,"state":y} for x,y in evaluation.freshness],
        claim=asdict(evaluation.claim) if evaluation.claim else None,next_action=evaluation.next_action,
        contract_digest=tx.contract.digest,snapshot=current.digest,evidence_set=evaluation.evidence_set,
        evidence=[asdict(item) for item in tx.evidence],assertions=[asdict(item) for item in tx.assertions],review_present=tx.review is not None,
        independence=evaluation.independence,last_confirmed=tx.last_confirmed)
    authority = {"class": "operator_confirmed", "source": "local-operator", "identity": "unverified",
                 "confirmed": tx.authorized, "read": list(tx.contract.authority.read),
                 "write": list(tx.contract.authority.write), "presets": list(tx.contract.authority.presets),
                 "binding": authority_digest(tx.contract.digest, tx.registry_record)}
    review = None
    if tx.review:
        exact = (tx.review.contract_digest == tx.contract.digest and tx.review.snapshot_digest == current.digest and
                 tx.review.evidence_set == evaluation.evidence_set)
        review = {"verdict": tx.review.verdict,
                  "trust": "operator_confirmed" if tx.review.source.id == "local-operator" else tx.review.source.trust_class,
                  "source": tx.review.source.id, "identity": "unverified",
                  "independence": tx.review.independence, "freshness": "CURRENT" if exact else "STALE"}
    current_ids = [item for item, state in evaluation.freshness if state == "CURRENT"]
    stale_ids = [{"id": item, "freshness": state} for item, state in evaluation.freshness if state != "CURRENT"]
    result["authority"] = authority
    result["review"] = review
    result["handoff"] = {"task": {"id": tx.contract.id, "title": tx.contract.title},
        "current_state": evaluation.state, "authority": authority, "current_evidence": current_ids,
        "stale_evidence": stale_ids, "review": review, "claim_status": evaluation.decision,
        "blockers": list(evaluation.reasons), "next_permitted_action": evaluation.next_action}
    if tx.closure:
        result["closure"]=asdict(tx.closure)
    return result

def confirm(operator, operation, binding):
    try:
        result=(operator or LocalOperator()).confirm(operation,binding)
    except RuntimeError as error:
        if str(error) in ("human_confirmation_unavailable","human_confirmation_rejected"):
            return None,str(error)
        raise
    from .storage.replay import confirmation
    confirmation(result,binding)
    return result,None

def execute(store_path, operation, *, input_path=None, preset_id=None, operator=None, human=False):
    readonly=operation in ("evaluate","status","resume")
    if operation not in ("authorize","baseline","check","evaluate","status","resume","review","close","attest"):
        raise RuntimeError("unknown_action")
    store=LocalHmacStore(store_path)
    with store.locked(readonly=readonly):
        events=store.read();tx=replay(events);contract=tx.contract
        LocalHmacStore(store_path,contract.repository)
        outside(tx.input_path,contract.repository,store,"contract")
        registry = validate_record(tx.registry_record, contract) if tx.registry_record else REGISTRY
        if tx.registry_record:
            outside(tx.registry_record["config_path"],contract.repository,store,"preset_config")
        raw,current=capture(contract,tx.input_path,tx.registry_record)
        error=authority_error(contract,tx.baseline,raw,tx.input_hash,tx.registry_record) if tx.baseline else preset_error(raw,tx.registry_record) or ("authority_changed" if raw["contract_input"]!=tx.input_hash else None)
        assessed=evaluate(contract,current,tx.evidence,tx.review,authorized=tx.authorized,
            baselined=tx.baseline is not None,authority_error=error,interrupted=tx.started is not None,closed=tx.closure)
        if readonly:
            result=project(operation,tx,current,assessed)
            if not error and not tx.started and not tx.evidence:
                result["state"]=tx.state
                result["handoff"]["current_state"]=tx.state
            return result
        if tx.closure:
            return envelope(operation,task_id=contract.id,state="CLOSED" if not error and assessed.state=="CLOSED" else "STALE",
                decision="NOT_CLAIMABLE",reasons=("closed_requires_new_task",),next_action="new_task")
        if error or tx.started:
            return project(operation,tx,current,assessed)
        if operation=="authorize":
            if tx.state!="DRAFT":
                raise RuntimeError("authorize_requires_draft")
            receipt,reason=confirm(operator,"authorize",action_binding("authorize",authority_digest(contract.digest,tx.registry_record),None,None))
            if reason:
                return envelope(operation,task_id=contract.id,state="BLOCKED",decision="NOT_CLAIMABLE",reasons=(reason,),next_action="authorize")
            # Confirmation may have taken time; never authorize changed inputs.
            again,_=capture(contract,tx.input_path,tx.registry_record)
            if again!=raw:
                raise RuntimeError("inputs_changed_during_confirmation")
            store.append(events,"AUTHORITY_VALIDATED",{"confirmation":receipt},contract.id)
            return envelope(operation,task_id=contract.id,state="AUTHORIZED",next_action="baseline")
        if operation=="baseline":
            if tx.state!="AUTHORIZED":
                raise RuntimeError("baseline_requires_authorized")
            store.append(events,"BASELINE_CAPTURED",{"snapshot":raw,"identity":asdict(current)},contract.id)
            return envelope(operation,task_id=contract.id,state="BASELINED",snapshot=current.digest,next_action="check")
        if tx.baseline is None:
            return project(operation,tx,current,assessed)
        if operation=="check":
            preset=registry.get(preset_id)
            if preset.id not in contract.authority.presets:
                raise RuntimeError("preset_not_authorized")
            if not any(preset.id in criterion.evidence for criterion in contract.acceptance):
                raise RuntimeError("preset_has_no_criterion")
            start=time.time_ns()
            events.append(store.append(events,"CHECK_STARTED",{"snapshot":raw,"identity":asdict(current),
                "preset_id":preset.id,"started_ns":start},contract.id))
            journal=run(preset.id,contract.repository,registry)
            after,after_model=capture(contract,tx.input_path,tx.registry_record)
            reason="check_timeout" if journal["timeout"] else "check_output_limit" if journal["truncated"] else "check_failed" if journal["exit_code"]!=0 else "inputs_changed_during_check" if not permitted_outputs(raw,after,preset) else None
            sequence=len(events)+1
            item=Evidence("check-"+str(sequence),preset.id,"measured_local",EvidenceSource("local-preset","measured_local"),
                contract.digest,after_model if not reason else current,tuple(c.id for c in contract.acceptance if preset.id in c.evidence),"FAIL" if reason else "PASS",sequence,time.time_ns())
            events.append(store.append(events,"CHECK_COMPLETED",{"evidence":asdict(item),"journal":journal,"reason":reason, **({"snapshot":after,"identity":asdict(after_model)} if tx.registry_record else {})},contract.id))
            tx=replay(events)
            current=after_model if not reason else current
            result=project(operation,tx,current,evaluate(contract,current,tx.evidence,tx.review,authorized=True,baselined=True))
            result["state"]="BLOCKED" if reason else "EVIDENCED"
            result["reasons"]=[reason] if reason else []
            result["decision"]="NOT_EVALUATED" if not reason else "NOT_CLAIMABLE"
            return result
        if operation=="attest":
            path=outside(input_path,contract.repository,store,"assertion")
            value=bounded_json(path)
            fields(value,("schema","task_id","statement"))
            if (value["schema"]!="taskclosurekit/assertion/v2" or value["task_id"]!=contract.id or
                type(value["statement"]) is not str or not 1<=len(value["statement"])<=4096):
                raise RuntimeError("invalid_assertion")
            assertion=AgentAssertion("assertion-"+str(len(events)+1),identity(value["statement"]),
                EvidenceSource("agent-import","agent_attested"),contract.digest,current.digest,len(events)+1,time.time_ns())
            events.append(store.append(events,"AGENT_ASSERTION_RECORDED",{"assertion":asdict(assertion)},contract.id))
            tx=replay(events)
            return envelope(operation,task_id=contract.id,state=tx.state,next_action="evaluate",
                            assertion=asdict(assertion))
        if operation=="review":
            if not tx.evidence or any(x in assessed.reasons for x in ("missing_required_evidence","stale_evidence","required_evidence_failed")):
                return project(operation,tx,current,assessed)
            receipt=None
            if human:
                if input_path is not None:
                    raise RuntimeError("invalid_review_input")
                receipt,reason=confirm(operator,"review",action_binding("review",contract.digest,current.digest,assessed.evidence_set))
                if reason:
                    return envelope(operation,task_id=contract.id,state="BLOCKED",decision="NOT_CLAIMABLE",reasons=(reason,),next_action="review")
                source=EvidenceSource("local-operator","human_confirmed");verdict="approve"
            else:
                path=outside(input_path,contract.repository,store,"review")
                value=bounded_json(path)
                fields(value,("schema","task_id","contract_digest","snapshot_digest","evidence_set","verdict"))
                if value["schema"]!="taskclosurekit/review/v2" or value["task_id"]!=contract.id or value["contract_digest"]!=contract.digest or value["snapshot_digest"]!=current.digest or value["evidence_set"]!=assessed.evidence_set or value["verdict"] not in ("approve","reject"):
                    raise RuntimeError("invalid_agent_review")
                source=EvidenceSource("agent-import","agent_attested");verdict=value["verdict"]
            after,_=capture(contract,tx.input_path,tx.registry_record)
            if after!=raw:
                raise RuntimeError("inputs_changed_during_confirmation")
            review=Review(contract.digest,current.digest,assessed.evidence_set,verdict,source,
                "UNKNOWN" if contract.review_independence=="required" else "NOT_REQUIRED",len(events)+1)
            events.append(store.append(events,"REVIEW_RECORDED",{"review":asdict(review),"confirmation":receipt},contract.id))
            tx=replay(events)
            result=project(operation,tx,current,evaluate(contract,current,tx.evidence,tx.review,authorized=True,baselined=True))
            result["state"]="REVIEWED";result["decision"]="NOT_EVALUATED";result["reasons"]=[]
            result["next_action"]="evaluate"
            return result
        if operation=="close":
            if assessed.decision!="CLAIMABLE":
                return project(operation,tx,current,assessed)
            receipt,reason=confirm(operator,"close",action_binding("close",contract.digest,current.digest,assessed.evidence_set))
            if reason:
                return envelope(operation,task_id=contract.id,state="BLOCKED",decision="NOT_CLAIMABLE",reasons=(reason,),next_action="close")
            after,_=capture(contract,tx.input_path,tx.registry_record)
            if after!=raw:
                raise RuntimeError("inputs_changed_during_confirmation")
            closure=close(assessed,receipt,len(events)+1)
            events.append(store.append(events,"CLAIM_CLOSED",{"closure":asdict(closure),"confirmation":receipt},contract.id))
            tx=replay(events)
            return project(operation,tx,current,evaluate(contract,current,tx.evidence,tx.review,authorized=True,baselined=True,closed=tx.closure))
        raise RuntimeError("unknown_action")
