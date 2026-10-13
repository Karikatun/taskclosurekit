"""Host-injected v1 ports. No imports, network or trust are selected by JSON.

Providers are trusted host code. Their verified result asserts an externally
established fact; this module validates bindings, never proves that assertion.
"""
from dataclasses import asdict, dataclass
from typing import Protocol
from threading import RLock
import time
from ..domain.contract import fields, identity

API = "taskclosurekit/adapters/v1"

@dataclass(frozen=True)
class ProviderManifest:
    schema: str
    identity: str
    capabilities: tuple
    trust_requirements: tuple

@dataclass(frozen=True)
class ReviewRequest:
    task_id: str
    contract_digest: str
    scope_digest: str
    snapshot_digest: str
    evidence_set: str

@dataclass(frozen=True)
class VerifiedReview:
    request: ReviewRequest
    reviewer_agent: str
    reviewer_session: str
    creator_agents: tuple
    creator_sessions: tuple
    identity_verified: bool
    fresh_context_verified: bool
    proof_digest: str
    verdict: str

class HostReviewProvider:
    manifest = None
    def verify(self, selector, request):
        raise NotImplementedError

class RepositoryInspectionProvider(Protocol):
    manifest: ProviderManifest
    def capture(self, contract, input_path, registry_record=None, integration_record=None): ...

class ExecutionEvidenceProvider(Protocol):
    manifest: ProviderManifest
    def verify(self, selector, request): ...

@dataclass(frozen=True)
class ImmutableRootRequest:
    task_id: str
    root: str
    manifest_digest: str
    physical_digest: str
    provenance_digest: str

@dataclass(frozen=True)
class VerifiedImmutableRoot:
    request: ImmutableRootRequest
    provider_identity: str
    proof_digest: str
    issued_ns: int
    expires_ns: int
    source_authenticated: bool
    immutable_verified: bool
    coverage_complete: bool
    revoked: bool

@dataclass
class ImmutableRootGuardState:
    """Live host state, never journal data. Mutations obey the guard mutex.

    Generation increases on any invalidation, including revoke then restore.
    The host also protects physical root and provenance from external writers.
    """
    generation: int = 0
    active: bool = True
    revoked: bool = False

@dataclass(frozen=True)
class ProtectedImmutableRoot:
    proof: VerifiedImmutableRoot
    state: ImmutableRootGuardState
    mutex: object
    protection_authenticated: bool
    protection_complete: bool

ROOT_GUARD_MUTEX_TYPE=type(RLock())

class ImmutableRootProvider:
    manifest=None
    def verify(self, request, at_ns):
        """Read-only authenticated host proof of a complete immutable root lease.

        Host must enforce immutability for the entire lease, including external
        writers and nested metadata. JSON, permissions/stat and journal cannot.
        """
        raise NotImplementedError

    def protect(self, request, at_ns):
        """Acquire a complete authenticated root+provenance interval guard.

        Required capability: repository.immutable-root-guard. The returned
        built-in RLock synchronizes live state invalidation. Trusted host
        enforcement must also protect all external writers through the final
        append, not just while this callback runs. Holding this mutex prevents
        asynchronous revocation; reentrant invalidation increments generation.
        Protection is finite (proof expiry), host-owned and self-cleaning; no
        external release callback is supported after a final capture. This
        interface itself does not establish OS enforcement.
        """
        raise RuntimeError("immutable_dependency_guard_unavailable")

def validate_immutable_root(proof,request,provider_identity,at_ns):
    if type(proof) is not VerifiedImmutableRoot or type(proof.request) is not ImmutableRootRequest or proof.request!=request or proof.provider_identity!=provider_identity:
        raise RuntimeError("immutable_root_binding_mismatch")
    digest(proof.proof_digest)
    if (type(proof.issued_ns) is not int or type(proof.expires_ns) is not int or
        not 0<proof.issued_ns<=at_ns<proof.expires_ns or proof.expires_ns-proof.issued_ns>60*10**9 or
        proof.source_authenticated is not True or proof.immutable_verified is not True or
        proof.coverage_complete is not True or proof.revoked is not False):
        raise RuntimeError("immutable_root_proof_unknown")
    return proof

def validate_root_guard(guard,request,provider_identity,at_ns,generation=None):
    # Exact plain records and a built-in mutex: subclass properties/callbacks
    # and JSON cannot execute at the final pure validation boundary.
    if (type(guard) is not ProtectedImmutableRoot or type(guard.state) is not ImmutableRootGuardState or
            type(guard.mutex) is not ROOT_GUARD_MUTEX_TYPE or
            guard.protection_authenticated is not True or guard.protection_complete is not True):
        raise RuntimeError("immutable_dependency_guard_unavailable")
    state=guard.state
    if (type(state.generation) is not int or state.generation<0 or
            (generation is not None and state.generation!=generation) or
            state.active is not True or state.revoked is not False):
        raise RuntimeError("immutable_root_guard_invalidated")
    return validate_immutable_root(guard.proof,request,provider_identity,at_ns)

@dataclass(frozen=True)
class AuthorityRequest:
    task_id: str
    operation: str
    scope_digest: str
    binding: str

@dataclass(frozen=True)
class VerifiedCapability:
    request: AuthorityRequest
    actions: tuple
    issued_ns: int
    expires_ns: int
    revoked: bool
    externally_issued: bool
    identity_verified: bool
    nonce: str
    proof_digest: str

class AuthorityProvider:
    manifest = None
    def verify(self, selector, request, at_ns):
        raise NotImplementedError
    def consume(self, selector, capability):
        """Atomically reject replay across processes before returning success."""
        raise NotImplementedError

def digest(value):
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise RuntimeError("invalid_provider_digest")

def selector_value(value):
    if type(value) is not str or not 1 <= len(value) <= 128 or any(ord(c)<32 for c in value):
        raise RuntimeError("invalid_provider_selector")

def negotiate(provider, capability):
    manifest = getattr(provider, "manifest", None)
    if not isinstance(manifest, ProviderManifest) or manifest.schema != API:
        raise RuntimeError("unsupported_adapter_version")
    digest(manifest.identity)
    if (type(manifest.capabilities) is not tuple or len(manifest.capabilities) > 32 or
            any(type(c) is not str for c in manifest.capabilities) or capability not in manifest.capabilities or
            type(manifest.trust_requirements) is not tuple or manifest.trust_requirements != ("trusted-host-code",)):
        raise RuntimeError("adapter_capability_unavailable")
    return manifest

def review_request(contract, current, evidence_set):
    return ReviewRequest(contract.id, contract.digest,
        identity({"read":contract.authority.read,"write":contract.authority.write}),current.digest,evidence_set)

def validate_review(value, request):
    if not isinstance(value, VerifiedReview) or value.request != request:
        raise RuntimeError("host_review_binding_mismatch")
    digest(value.proof_digest)
    identities = (value.reviewer_agent,value.reviewer_session,*value.creator_agents,*value.creator_sessions)
    if (type(value.creator_agents) is not tuple or not value.creator_agents or
            type(value.creator_sessions) is not tuple or not value.creator_sessions or
            any(type(v) is not str or not 1<=len(v)<=128 or any(ord(c)<32 for c in v) for v in identities) or
            value.identity_verified is not True or value.fresh_context_verified is not True or
            value.reviewer_agent in value.creator_agents or value.reviewer_session in value.creator_sessions or
            value.verdict not in ("approve","reject")):
        raise RuntimeError("host_review_not_independent")
    return value

def verify_review(provider, selector, request):
    if not isinstance(provider, HostReviewProvider):
        raise RuntimeError("trusted_host_review_provider_required")
    negotiate(provider,"review.context"); selector_value(selector)
    return validate_review(provider.verify(selector,request), request)

def review_from_record(value):
    fields(value, ("request","reviewer_agent","reviewer_session","creator_agents","creator_sessions",
        "identity_verified","fresh_context_verified","proof_digest","verdict"))
    fields(value["request"], ("task_id","contract_digest","scope_digest","snapshot_digest","evidence_set"))
    if type(value["creator_agents"]) is not list or type(value["creator_sessions"]) is not list:
        raise RuntimeError("invalid_host_review")
    request = ReviewRequest(**value["request"])
    for k in ("contract_digest","scope_digest","snapshot_digest","evidence_set"):
        digest(getattr(request,k))
    result = VerifiedReview(request,value["reviewer_agent"],value["reviewer_session"],tuple(value["creator_agents"]),
        tuple(value["creator_sessions"]),value["identity_verified"],value["fresh_context_verified"],value["proof_digest"],value["verdict"])
    return validate_review(result,request)

def authority_request(contract, operation, binding):
    return AuthorityRequest(contract.id,operation,identity({"read":contract.authority.read,"write":contract.authority.write,"presets":contract.authority.presets}),binding)

def validate_capability(value, request, at_ns):
    if not isinstance(value, VerifiedCapability) or value.request != request:
        raise RuntimeError("capability_binding_mismatch")
    digest(value.proof_digest); selector_value(value.nonce)
    if (type(value.actions) is not tuple or value.actions != (request.operation,) or request.operation not in ("authorize","close") or
            type(value.issued_ns) is not int or type(value.expires_ns) is not int or type(at_ns) is not int or
            not 0 < value.issued_ns <= at_ns < value.expires_ns or value.expires_ns-value.issued_ns > 86400*10**9 or
            value.revoked is not False or value.externally_issued is not True or value.identity_verified is not True):
        raise RuntimeError("capability_expired_revoked_or_unverified")
    return value

def verify_authority(provider, selector, request, at_ns=None):
    if not isinstance(provider, AuthorityProvider):
        raise RuntimeError("external_authority_provider_required")
    negotiate(provider,"authority.one-time"); selector_value(selector)
    at_ns = time.time_ns() if at_ns is None else at_ns
    return validate_capability(provider.verify(selector,request,at_ns),request,at_ns)

def capability_from_record(value, request, at_ns):
    fields(value,("request","actions","issued_ns","expires_ns","revoked","externally_issued","identity_verified","nonce","proof_digest"))
    fields(value["request"],("task_id","operation","scope_digest","binding"))
    if type(value["actions"]) is not list:
        raise RuntimeError("invalid_capability")
    return validate_capability(VerifiedCapability(AuthorityRequest(**value["request"]),tuple(value["actions"]),
        value["issued_ns"],value["expires_ns"],value["revoked"],value["externally_issued"],value["identity_verified"],value["nonce"],value["proof_digest"]),request,at_ns)

def capabilities():
    return {"schema":API,"cli":"taskclosurekit/result/v2","repository":"bounded-local",
        "execution":"trusted-presets","roots":"bounded-host-proof-or-rehash","capture_budget":"trusted-finite-v2","stages":"explicit-content-equivalence",
        "review":"host-injected-verified-context","authority":"external-one-time-capability",
        "external_ci":"host-injected-port","immutable_root":"host-injected-protected-interval",
        "limitations":["no-host-sandbox","no-network-provider", "no-production-immutable-root-provider", "no-json-trust-upgrade"]}
