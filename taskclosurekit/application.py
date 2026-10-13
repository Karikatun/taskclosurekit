"""Transaction application service; IO and trust adapters live outside domain."""
from dataclasses import asdict, replace
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
from .execution.presets import REGISTRY, prepare_config, validate_record
from .integration.policy import task_authority_digest as authority_digest, prepare_config as prepare_integration
from .snapshots.repository import capture, authority_error, preset_error, equivalent_capture
from .storage.local_hmac import LocalHmacStore
from .storage.replay import replay
from .trust.local_operator import LocalOperator


def envelope(operation, *, task_id=None, state="INVALID", decision="NOT_EVALUATED", reasons=(), status="ok", **extra):
    from .integration.adapters import capabilities
    return {"schema":"taskclosurekit/result/v2", "operation":operation,
            "operational":{"status":status}, "task_id":task_id, "state":state,
            "decision":decision, "reasons":list(reasons), "freshness":[], "claim":None,
            "next_action":"none", "capabilities":capabilities(), **extra}

def outside(path, repo, store, kind):
    path=snapshot.safe_path(path)
    if snapshot.path_within(path,repo):
        raise RuntimeError(kind+"_input_must_be_outside_repository")
    if snapshot.path_within(path,store.path):
        raise RuntimeError(kind+"_input_must_be_outside_store")
    return path

def create(store_path, input_path, *, preset_config=None, integration_config=None, dependency_provider=None):
    path=snapshot.safe_path(input_path)
    value=bounded_json(path)
    contract=parse_contract(value)
    repo=snapshot.safe_path(contract.repository)
    if not repo.is_dir():
        raise RuntimeError("repository_required")
    store=LocalHmacStore(store_path,repo)
    outside(path,repo,store,"contract")
    capture_budget=None
    if integration_config is not None:
        preliminary=bounded_json(outside(integration_config,repo,store,"integration_config"))
        if preliminary.get("schema")=="taskclosurekit/integration/v2":
            from .integration.budgets import validate_budget
            capture_budget=validate_budget(preliminary.get("capture_budget"))
    registry_record = prepare_config(preset_config, contract, store,capture_budget=capture_budget) if preset_config is not None else None
    integration_record = prepare_integration(integration_config, contract, store, registry_record,dependency_provider) if integration_config is not None else None
    if registry_record is None:
        for preset_id in contract.authority.presets:
            REGISTRY.get(preset_id)
    identity=snapshot.regular(path,snapshot.Budget(),65536)[0]
    # Re-read to prevent contract bytes and input identity disagreeing under a race.
    if bounded_json(path)!=value or snapshot.regular(path,snapshot.Budget(),65536)[0]!=identity:
        raise RuntimeError("contract_input_race")
    from .integration.dependencies import GuardSession
    with GuardSession(dependency_provider) as guard:
        guard.bind(integration_record,contract)
        with store.locked(create=True):
            store.initialize()
            guard.validate()
            store.append([],"CONTRACT_CREATED",{"contract":value,"input_path":str(path),"input_hash":identity, **({"registry":registry_record} if registry_record else {}), **({"integration":integration_record} if integration_record else {})},contract.id)
    return envelope("task.create",task_id=contract.id,state="DRAFT",next_action="authorize",contract_digest=contract.digest, authority_digest=authority_digest(contract.digest,registry_record,integration_record))

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
                 "binding": authority_digest(tx.contract.digest, tx.registry_record, tx.integration_record)}
    if tx.delegated_authority:
        authority.update({"class":"delegated", "source":"external-authority-provider",
            "identity":"externally_verified", "provider_identity":tx.delegated_authority["provider_identity"],
            "scope_digest":tx.delegated_authority["capability"]["request"]["scope_digest"]})
    review = None
    if tx.review:
        exact = (tx.review.contract_digest == tx.contract.digest and tx.review.snapshot_digest == current.digest and
                 tx.review.evidence_set == evaluation.evidence_set)
        review = {"verdict": tx.review.verdict,
                  "trust": "operator_confirmed" if tx.review.source.id == "local-operator" else tx.review.source.trust_class,
                  "source": tx.review.source.id, "identity": "unverified",
                  "independence": tx.review.independence, "freshness": "CURRENT" if exact else "STALE"}
        if tx.host_review:
            proof=tx.host_review["proof"]
            review.update({"identity":"host_verified" if tx.review.independence=="PROVEN" else "unverified",
                "agent":proof.reviewer_agent,"session":proof.reviewer_session,
                "provider_identity":tx.host_review["provider_identity"],"proof_digest":proof.proof_digest})
    current_ids = [item for item, state in evaluation.freshness if state == "CURRENT"]
    stale_ids = [{"id": item, "freshness": state} for item, state in evaluation.freshness if state != "CURRENT"]
    result["authority"] = authority
    result["review"] = review
    result["handoff"] = {"task": {"id": tx.contract.id, "title": tx.contract.title},
        "current_state": evaluation.state, "authority": authority, "current_evidence": current_ids,
        "stale_evidence": stale_ids, "review": review, "claim_status": evaluation.decision,
        "blockers": list(evaluation.reasons), "next_permitted_action": evaluation.next_action}
    if tx.delegated_authority and not tx.authorized:
        result["reasons"].append("external_authority_provider_unavailable")
        result["next_action"]="trusted_authority_provider_required"
        result["handoff"]["blockers"]=result["reasons"]
        result["handoff"]["next_permitted_action"]=result["next_action"]
    if tx.closure:
        result["closure"]=asdict(tx.closure)
    if tx.close_attempt:
        result["authority_attempt"]={"operation":"close","status":tx.close_attempt["status"],
            "binding":tx.close_attempt["binding"],"nonce":tx.close_attempt["authorization"]["capability"]["nonce"],
            "provider_identity":tx.close_attempt["authorization"]["provider_identity"],"recovery":"inspect_then_new_task"}
    from .snapshots.repository import result_identity
    result["result_identity"] = result_identity(tx.contract, current, tx.integration_record)
    result["result_identity"]["local_ready"] = evaluation.decision == "CLAIMABLE"
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

def execute(store_path, operation, *, input_path=None, preset_id=None, operator=None, human=False,
            review_provider=None, provider_selector=None, authority_provider=None, capability_selector=None,dependency_provider=None):
    from .integration.dependencies import GuardSession
    with GuardSession(dependency_provider) as guard:
        return guarded_execute(store_path,operation,input_path=input_path,preset_id=preset_id,operator=operator,human=human,
            review_provider=review_provider,provider_selector=provider_selector,authority_provider=authority_provider,
            capability_selector=capability_selector,dependency_provider=guard)

def guarded_execute(store_path, operation, *, input_path=None, preset_id=None, operator=None, human=False,
            review_provider=None, provider_selector=None, authority_provider=None, capability_selector=None,dependency_provider=None):
    readonly=operation in ("evaluate","status","resume")
    if operation not in ("authorize","baseline","check","evaluate","status","resume","review","close","attest"):
        raise RuntimeError("unknown_action")
    store=LocalHmacStore(store_path)
    with store.locked(readonly=readonly):
        events=store.read();tx=replay(events);contract=tx.contract
        if tx.delegated_authority:
            if authority_provider is None:
                tx.authorized=False
            else:
                from .integration.adapters import verify_authority, capability_from_record, authority_request
                saved=tx.delegated_authority
                request=authority_request(contract,"authorize",saved["capability"]["request"]["binding"])
                expected=capability_from_record(saved["capability"],request,saved["recorded_ns"])
                if (authority_provider.manifest.identity!=saved["provider_identity"] or
                        verify_authority(authority_provider,saved["selector"],request,saved["recorded_ns"])!=expected):
                    raise RuntimeError("external_authority_proof_changed")
        if tx.host_review:
            from .integration.adapters import verify_review
            if review_provider is None:
                tx.review=replace(tx.review,independence="UNKNOWN")
            else:
                expected=tx.host_review["proof"]
                if (review_provider.manifest.identity!=tx.host_review["provider_identity"] or
                        verify_review(review_provider,tx.host_review["selector"],expected.request)!=expected):
                    raise RuntimeError("host_review_proof_changed")
        LocalHmacStore(store_path,contract.repository)
        outside(tx.input_path,contract.repository,store,"contract")
        registry = validate_record(tx.registry_record, contract) if tx.registry_record else REGISTRY
        if tx.registry_record:
            outside(tx.registry_record["config_path"],contract.repository,store,"preset_config")
        if tx.integration_record:
            outside(tx.integration_record["config_path"],contract.repository,store,"integration_config")
        dependency_provider.bind(tx.integration_record,contract)
        def append(events,kind,payload,task_id):
            # One-time audit records remain durable even if protection was
            # invalidated after consumption; they never assert successful close.
            if kind not in ("CLOSE_CAPABILITY_CONSUMED","CLOSE_CAPABILITY_REFUSED"):
                dependency_provider.validate()
            return store.append(events,kind,payload,task_id)
        raw,current=capture(contract,tx.input_path,tx.registry_record,tx.integration_record,dependency_provider)
        error=authority_error(contract,tx.baseline,raw,tx.input_hash,tx.registry_record,tx.integration_record) if tx.baseline else preset_error(raw,tx.registry_record) or ("authority_changed" if raw["contract_input"]!=tx.input_hash else None)
        if tx.close_attempt:
            error=error or "capability_attempt_requires_recovery"
        if tx.integration_record and raw["integration"]["config_binding"] != tx.integration_record["config_binding"]:
            error="integration_authority_changed"
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
            binding=action_binding("authorize",authority_digest(contract.digest,tx.registry_record,tx.integration_record),None,None)
            authorization=None
            if authority_provider is not None:
                if operator is not None:
                    raise RuntimeError("ambiguous_authority_mechanism")
                from .integration.adapters import verify_authority, authority_request
                recorded_ns=time.time_ns()
                capability=verify_authority(authority_provider,capability_selector,authority_request(contract,"authorize",binding),recorded_ns)
                authorization={"capability":asdict(capability),"selector":capability_selector,"provider_identity":authority_provider.manifest.identity,"recorded_ns":recorded_ns}
                receipt,reason=None,None
            else:
                if capability_selector is not None:
                    raise RuntimeError("external_authority_provider_required")
                receipt,reason=confirm(operator,"authorize",binding)
            if reason:
                return envelope(operation,task_id=contract.id,state="BLOCKED",decision="NOT_CLAIMABLE",reasons=(reason,),next_action="authorize")
            # Confirmation may have taken time; never authorize changed inputs.
            again,_=capture(contract,tx.input_path,tx.registry_record,tx.integration_record,dependency_provider)
            if not equivalent_capture(again,raw):
                raise RuntimeError("inputs_changed_during_confirmation")
            if authorization:
                from .integration.adapters import validate_capability
                validate_capability(capability,capability.request,time.time_ns())
                authority_provider.consume(capability_selector,capability)
            append(events,"AUTHORITY_VALIDATED",{"authorization":authorization} if authorization else {"confirmation":receipt},contract.id)
            return envelope(operation,task_id=contract.id,state="AUTHORIZED",next_action="baseline")
        if operation=="baseline":
            if not tx.authorized:
                return project(operation,tx,current,assessed)
            if tx.state!="AUTHORIZED":
                raise RuntimeError("baseline_requires_authorized")
            append(events,"BASELINE_CAPTURED",{"snapshot":raw,"identity":asdict(current)},contract.id)
            return envelope(operation,task_id=contract.id,state="BASELINED",snapshot=current.digest,next_action="check")
        if tx.baseline is None:
            return project(operation,tx,current,assessed)
        if not tx.authorized:
            return project(operation,tx,current,assessed)
        if operation=="check":
            preset=registry.get(preset_id)
            if preset.id not in contract.authority.presets:
                raise RuntimeError("preset_not_authorized")
            if not any(preset.id in criterion.evidence for criterion in contract.acceptance):
                raise RuntimeError("preset_has_no_criterion")
            start=time.time_ns()
            events.append(append(events,"CHECK_STARTED",{"snapshot":raw,"identity":asdict(current),
                "preset_id":preset.id,"started_ns":start},contract.id))
            journal=run(preset.id,contract.repository,registry)
            after,after_model=capture(contract,tx.input_path,tx.registry_record,tx.integration_record,dependency_provider)
            from .execution.runner import result_reason
            reason = result_reason(journal,preset) or ("inputs_changed_during_check" if not permitted_outputs(raw,after,preset) else None)
            sequence=len(events)+1
            item=Evidence("check-"+str(sequence),preset.id,"measured_local",EvidenceSource("local-preset","measured_local"),
                contract.digest,after_model if not reason else current,tuple(c.id for c in contract.acceptance if preset.id in c.evidence),"FAIL" if reason else "PASS",sequence,time.time_ns())
            events.append(append(events,"CHECK_COMPLETED",{"evidence":asdict(item),"journal":journal,"reason":reason, **({"snapshot":after,"identity":asdict(after_model)} if tx.registry_record else {})},contract.id))
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
            events.append(append(events,"AGENT_ASSERTION_RECORDED",{"assertion":asdict(assertion)},contract.id))
            tx=replay(events)
            return envelope(operation,task_id=contract.id,state=tx.state,next_action="evaluate",
                            assertion=asdict(assertion))
        if operation=="review":
            if not tx.evidence or any(x in assessed.reasons for x in ("missing_required_evidence","stale_evidence","required_evidence_failed")):
                return project(operation,tx,current,assessed)
            receipt=None
            host_proof=None
            if review_provider is not None:
                if human or input_path is not None:
                    raise RuntimeError("invalid_review_input")
                from .integration.adapters import verify_review, review_request
                host_proof=verify_review(review_provider,provider_selector,review_request(contract,current,assessed.evidence_set))
                source=EvidenceSource("host-review","host_verified");verdict=host_proof.verdict
            elif human:
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
            after,_=capture(contract,tx.input_path,tx.registry_record,tx.integration_record,dependency_provider)
            if not equivalent_capture(after,raw):
                raise RuntimeError("inputs_changed_during_confirmation")
            review=Review(contract.digest,current.digest,assessed.evidence_set,verdict,source,
                "PROVEN" if host_proof else "UNKNOWN" if contract.review_independence=="required" else "NOT_REQUIRED",len(events)+1)
            events.append(append(events,"REVIEW_RECORDED",{"review":asdict(review),"confirmation":receipt,
                **({"host_proof":asdict(host_proof),"selector":provider_selector,"provider_identity":review_provider.manifest.identity} if host_proof else {}),
                **({"snapshot":raw,"identity":asdict(current)} if tx.integration_record else {})},contract.id))
            tx=replay(events)
            result=project(operation,tx,current,evaluate(contract,current,tx.evidence,tx.review,authorized=True,baselined=True))
            result["state"]="REVIEWED";result["decision"]="NOT_EVALUATED";result["reasons"]=[]
            result["next_action"]="evaluate"
            return result
        if operation=="close":
            if assessed.decision!="CLAIMABLE":
                return project(operation,tx,current,assessed)
            binding=action_binding("close",contract.digest,current.digest,assessed.evidence_set)
            authorization=None
            if authority_provider is not None:
                if operator is not None:
                    raise RuntimeError("ambiguous_authority_mechanism")
                from .integration.adapters import verify_authority, authority_request
                recorded_ns=time.time_ns()
                capability=verify_authority(authority_provider,capability_selector,authority_request(contract,"close",binding),recorded_ns)
                authorization={"capability":asdict(capability),"selector":capability_selector,"provider_identity":authority_provider.manifest.identity,"recorded_ns":recorded_ns}
                receipt,reason=None,None
            else:
                if capability_selector is not None:
                    raise RuntimeError("external_authority_provider_required")
                receipt,reason=confirm(operator,"close",binding)
            if reason:
                return envelope(operation,task_id=contract.id,state="BLOCKED",decision="NOT_CLAIMABLE",reasons=(reason,),next_action="close")
            after,_=capture(contract,tx.input_path,tx.registry_record,tx.integration_record,dependency_provider)
            if not equivalent_capture(after,raw):
                raise RuntimeError("inputs_changed_during_confirmation")
            if tx.host_review:
                from .integration.adapters import verify_review
                expected=tx.host_review["proof"]
                if verify_review(review_provider,tx.host_review["selector"],expected.request)!=expected:
                    raise RuntimeError("host_review_proof_changed")
            if authorization:
                from .domain.claim import Closure
                from .integration.adapters import validate_capability
                validate_capability(capability,capability.request,time.time_ns())
                # Consumption is the external provider's atomic one-time boundary.
                # A later interrupted append never retries the capability silently.
                events.append(append(events,"CLOSE_CAPABILITY_STARTED",{"authorization":authorization,"snapshot":raw,"identity":asdict(current)},contract.id))
                authority_provider.consume(capability_selector,capability)
                events.append(append(events,"CLOSE_CAPABILITY_CONSUMED",{"binding":binding},contract.id))
            # All external callbacks have finished. Capture and pure gates are
            # adjacent to append; no provider callback can write after binding.
            final_raw,final_current=capture(contract,tx.input_path,tx.registry_record,tx.integration_record,dependency_provider)
            final=evaluate(contract,final_current,tx.evidence,tx.review,authorized=tx.authorized,baselined=True,
                authority_error=authority_error(contract,tx.baseline,final_raw,tx.input_hash,tx.registry_record,tx.integration_record))
            if tx.integration_record:
                from .integration.dependencies import validate_observation as validate_dependencies
                validate_dependencies(final_raw.get("dependency_proofs"),tx.integration_record,contract,time.time_ns(),final_raw["entries"])
            authorization_current=True
            if authorization:
                try:
                    validate_capability(capability,capability.request,time.time_ns())
                except RuntimeError:
                    authorization_current=False
            if not authorization_current or final.decision!="CLAIMABLE" or action_binding("close",contract.digest,final_current.digest,final.evidence_set)!=binding:
                refusal="capability_expired_after_authority_callback" if not authorization_current else "inputs_changed_after_authority_callback"
                if authorization:
                    events.append(append(events,"CLOSE_CAPABILITY_REFUSED",{"binding":binding,"reason":refusal},contract.id))
                return envelope(operation,task_id=contract.id,state="BLOCKED",decision="NOT_CLAIMABLE",reasons=(refusal,),next_action="inspect_then_new_task")
            raw,current,assessed=final_raw,final_current,final
            if authorization:
                closure=Closure(assessed.claim,"delegated-authority",binding,len(events)+1,True)
            else:
                closure=close(assessed,receipt,len(events)+1)
            events.append(append(events,"CLAIM_CLOSED",{"closure":asdict(closure),
                **({"authorization":authorization,"consume_audit":True} if authorization else {"confirmation":receipt}),
                **({"snapshot":raw,"identity":asdict(current)} if tx.integration_record else {})},contract.id))
            tx=replay(events)
            return project(operation,tx,current,evaluate(contract,current,tx.evidence,tx.review,authorized=True,baselined=True,closed=tx.closure))
        raise RuntimeError("unknown_action")
