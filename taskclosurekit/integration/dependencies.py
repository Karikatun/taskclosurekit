"""Material dependency byte bindings and authenticated immutable-root leases.

No production provider is installed. Trusted host injection is the trust crossing;
persisted manifests are data and never substitute for a current host proof.
"""
from dataclasses import asdict
from contextlib import contextmanager
from pathlib import Path
import stat
import time
from ..domain.contract import fields,identity,path
from .._primitives import snapshot,policy
from .adapters import (ImmutableRootProvider,ImmutableRootRequest,VerifiedImmutableRoot,
    negotiate,validate_immutable_root,validate_root_guard,digest)

def physical(root):
    root=snapshot.safe_path(root);info=root.lstat()
    if not stat.S_ISDIR(info.st_mode):raise RuntimeError("immutable_root_directory_required")
    return {"device":info.st_dev,"inode":info.st_ino,"mode":stat.S_IMODE(info.st_mode)}

def request(contract,name,binding):
    return ImmutableRootRequest(contract.id,str(Path(contract.repository)/name),identity(binding["entries"]),
        identity(binding["physical"]),identity(binding["provenance"]))

def verify(provider,expected,provider_identity):
    if not isinstance(provider,ImmutableRootProvider):raise RuntimeError("immutable_dependency_proof_unavailable")
    manifest=negotiate(provider,"repository.immutable-root")
    if manifest.identity!=provider_identity:raise RuntimeError("immutable_root_binding_mismatch")
    at_ns=time.time_ns()
    return validate_immutable_root(provider.verify(expected,at_ns),expected,provider_identity,at_ns)

class GuardSession:
    """Hold trusted interval guards through actual use and required append.

    Provider callbacks run only at binding, before capture. All validation
    after capture is core IO/plain data; release uses built-in mutexes only.
    A finite self-cleaning host lease avoids a release callback trust race.
    """
    def __init__(self,provider):
        self.provider=provider;self.guards={};self.record=None;self.contract=None
    def __enter__(self):return self
    def __exit__(self,*unused):
        for guard,generation in reversed(tuple(self.guards.values())):
            if guard.mutex._is_owned():guard.mutex.release()
        self.guards.clear()
    def bind(self,record,contract):
        if self.record is not None:
            if self.record!=record or self.contract!=contract:raise RuntimeError("immutable_root_binding_mismatch")
            return
        self.record=record;self.contract=contract
        for name,binding in (record or {}).get("dependency_bindings",{}).items():
            provider=self.provider
            if not isinstance(provider,ImmutableRootProvider):raise RuntimeError("immutable_dependency_guard_unavailable")
            try:manifest=negotiate(provider,"repository.immutable-root-guard")
            except RuntimeError:raise RuntimeError("immutable_dependency_guard_unavailable") from None
            if manifest.identity!=binding["provider_identity"]:raise RuntimeError("immutable_root_binding_mismatch")
            expected=request(contract,name,binding);at_ns=time.time_ns()
            guard=provider.protect(expected,at_ns)
            validate_root_guard(guard,expected,manifest.identity,time.time_ns())
            # Register before acquire, so an interrupt at the acquire return
            # boundary still releases any mutex now owned by this thread.
            if guard.mutex._is_owned():raise RuntimeError("immutable_dependency_guard_unavailable")
            self.guards[name]=(guard,guard.state.generation)
            remaining=(guard.proof.expires_ns-time.time_ns())/10**9
            if remaining<=0 or not guard.mutex.acquire(timeout=remaining):raise RuntimeError("immutable_root_guard_unavailable")
        self.validate()
    def validate(self):
        for name,binding in (self.record or {}).get("dependency_bindings",{}).items():
            root=Path(self.contract.repository)/name
            if physical(root)!=binding["physical"]:raise RuntimeError("immutable_root_binding_mismatch")
            budget=snapshot.Budget(self.record["policy"]["capture_budget"])
            if any(snapshot.regular(snapshot.safe_path(Path(self.contract.repository)/p),budget)[0]!=entry for p,entry in binding["provenance"].items()):
                raise RuntimeError("immutable_dependency_provenance_changed")
            guard,generation=self.guards[name]
            validate_root_guard(guard,request(self.contract,name,binding),binding["provider_identity"],time.time_ns(),generation)
    def observe(self):
        self.validate()
        proofs={name:asdict(guard.proof) for name,(guard,generation) in self.guards.items()}
        cached={name:{"entries":binding["entries"],"physical":binding["physical"]}
            for name,binding in (self.record or {}).get("dependency_bindings",{}).items()}
        return cached,{"captured_ns":time.time_ns(),"roots":proofs} if proofs else None

@contextmanager
def protected_capture(record,contract,provider):
    if type(provider) is GuardSession:
        provider.bind(record,contract);yield provider
    else:
        with GuardSession(provider) as session:
            session.bind(record,contract);yield session

def prepare(roots,contract,capture_budget,provider):
    result={};budget=snapshot.Budget(capture_budget)
    for root in roots:
        if root["type"]!="dependency" or root["dependency_provenance"]["method"]!="immutable-host":continue
        if not isinstance(provider,ImmutableRootProvider):raise RuntimeError("immutable_dependency_proof_unavailable")
        manifest=negotiate(provider,"repository.immutable-root");name=root["path"]
        before=physical(Path(contract.repository)/name)
        entries=snapshot.inventory(Path(contract.repository)/name,budget)
        provenance={p:snapshot.regular(snapshot.safe_path(Path(contract.repository)/p),budget)[0]
            for p in root["dependency_provenance"]["identity_inputs"]}
        if before!=physical(Path(contract.repository)/name):raise RuntimeError("snapshot_race")
        binding={"entries":entries,"physical":before,"provenance":provenance,"provider_identity":manifest.identity}
        verify(provider,request(contract,name,binding),manifest.identity)
        result[name]=binding
    return result

def validate_bindings(bindings,record):
    expected={r["path"] for r in record["policy"]["roots"] if r["type"]=="dependency" and r["dependency_provenance"]["method"]=="immutable-host"}
    if type(bindings) is not dict or set(bindings)!=expected:raise RuntimeError("invalid_dependency_binding")
    limits=record["policy"]["capture_budget"]
    for name,binding in bindings.items():
        fields(binding,("entries","physical","provenance","provider_identity"));digest(binding["provider_identity"])
        fields(binding["physical"],("device","inode","mode"))
        if any(type(n) is not int or n<0 for n in binding["physical"].values()) or binding["physical"]["mode"]>0o7777:raise RuntimeError("invalid_dependency_binding")
        if type(binding["entries"]) is not dict or len(binding["entries"])>limits["entries"]:raise RuntimeError("invalid_dependency_binding")
        for relative,entry in binding["entries"].items():
            policy.relative_path(relative);policy.valid_entry(entry,limits["file_bytes"])
        if type(binding["provenance"]) is not dict:raise RuntimeError("invalid_dependency_binding")
        expected_inputs=next(r["dependency_provenance"]["identity_inputs"] for r in record["policy"]["roots"] if r["path"]==name)
        if set(binding["provenance"])!=set(expected_inputs):raise RuntimeError("invalid_dependency_binding")
        for p,entry in binding["provenance"].items():path(p);policy.valid_entry(entry,limits["file_bytes"])

def observe(record,contract,provider):
    if type(provider) is not GuardSession:raise RuntimeError("immutable_dependency_guard_unavailable")
    provider.bind(record,contract)
    return provider.observe()

def validate_observation(value,record,contract,at_ns=None,entries=None):
    bindings=record.get("dependency_bindings",{})
    if not bindings:
        if value is not None:raise RuntimeError("invalid_dependency_binding")
        return
    fields(value,("captured_ns","roots"))
    if type(value["captured_ns"]) is not int or value["captured_ns"]<=0 or type(value["roots"]) is not dict or set(value["roots"])!=set(bindings):raise RuntimeError("invalid_dependency_binding")
    for name,p in value["roots"].items():
        if entries is not None:
            expected={name+"/"+n:e for n,e in bindings[name]["entries"].items()}
            actual={n:e for n,e in entries.items() if n.startswith(name+"/")}
            if actual!=expected or entries.get(name)!={"kind":"directory","mode":bindings[name]["physical"]["mode"]}:
                raise RuntimeError("invalid_dependency_binding")
        fields(p,("request","provider_identity","proof_digest","issued_ns","expires_ns","source_authenticated","immutable_verified","coverage_complete","revoked"))
        fields(p["request"],("task_id","root","manifest_digest","physical_digest","provenance_digest"))
        proof=VerifiedImmutableRoot(ImmutableRootRequest(**p["request"]),**{k:v for k,v in p.items() if k!="request"})
        validate_immutable_root(proof,request(contract,name,bindings[name]),bindings[name]["provider_identity"],value["captured_ns"] if at_ns is None else at_ns)
