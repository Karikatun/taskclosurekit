"""A trusted-host lease adapter exercises the boundary, not OS immutability."""
from dataclasses import replace
import json
from pathlib import Path
import time
from threading import RLock
import unittest
from unittest.mock import patch
import test_integration_contract as fixture
from taskclosurekit import application
from taskclosurekit._primitives import snapshot
from taskclosurekit.integration.adapters import (ImmutableRootProvider,ProviderManifest,VerifiedImmutableRoot,
    ImmutableRootGuardState,ProtectedImmutableRoot)

class TrustedTestHost(ImmutableRootProvider):
    manifest=ProviderManifest("taskclosurekit/adapters/v1","f"*64,("repository.immutable-root","repository.immutable-root-guard"),("trusted-host-code",))
    def __init__(self):self._change={};self.guards=[];self.requests={}
    @property
    def change(self):return self._change
    @change.setter
    def change(self,value):
        self._change=value
        for guard in self.guards:
            with guard.mutex:
                guard.state.generation+=1;guard.state.revoked=value.get("revoked",False)
    def verify(self,request,at_ns):
        # Assumed externally enforced lease. This test class is deliberately
        # not a production filesystem immutability/completeness implementation.
        self.requests.setdefault(request.root,request)
        if request!=self.requests[request.root]:raise RuntimeError("host_root_request_not_authenticated")
        return replace(VerifiedImmutableRoot(request,self.manifest.identity,"e"*64,at_ns-1,at_ns+30*10**9,True,True,True,False),**self.change)
    def protect(self,request,at_ns):
        guard=ProtectedImmutableRoot(self.verify(request,at_ns),ImmutableRootGuardState(),RLock(),True,True)
        self.guards.append(guard)
        return guard

class ImmutableRootsTests(unittest.TestCase):
    def setUp(self):
        self.case=fixture.IntegrationContractTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.host=TrustedTestHost();self.host.change={}
        root=self.case.repo/"dependencies";root.mkdir();(root/".ignored-input").write_bytes(b'a'*(2*1024*1024))
        (self.case.repo/".gitignore").write_text("dependencies/\n")
        self.case.value["authority"]["read"].append("dependencies")
        self.case.input.write_text(json.dumps(self.case.value))
        self.case.policy.update(schema="taskclosurekit/integration/v2",coverage="bounded-host-proof-or-rehash",
            capture_budget={"file_bytes":16*1024*1024,"aggregate_bytes":256*1024*1024,"entries":20000,"seconds":30})
        self.case.policy["roots"].append({"path":"dependencies","type":"dependency","acceptance":"include",
            "rationale":"Material installed inputs under externally enforced complete immutable lease.","dependency_provenance":{"method":"immutable-host","identity_inputs":["build.json"]}})
        self.case.integration.write_text(json.dumps(self.case.policy))
    def action(self,op,**kwargs):
        return self.case.action(op,dependency_provider=self.host,**kwargs)
    def prepare(self):
        application.create(str(self.case.store),str(self.case.input),preset_config=str(self.case.config),
            integration_config=str(self.case.integration),dependency_provider=self.host)
        self.action("authorize");self.action("baseline")
        self.action("check",preset_id="test");self.action("check",preset_id="build");self.action("review",human=True)
    def test_actual_dependency_bytes_bound_once_then_unchanged_reads_reuse_verified_manifest(self):
        self.prepare();reads=[];original=snapshot.regular
        def counted(path,*args,**kwargs):
            if str(path).endswith(".ignored-input"):reads.append(str(path))
            return original(path,*args,**kwargs)
        with patch.object(snapshot,"regular",side_effect=counted):
            for unused in range(2):self.assertEqual(self.action("evaluate")["state"],"CLAIMABLE")
        self.assertEqual(reads,[])
        with self.assertRaisesRegex(RuntimeError,"immutable_dependency_guard_unavailable"):
            self.case.action("close")
        self.assertEqual(self.action("close")["state"],"CLOSED")

    def test_nine_megabyte_dependency_material_bytes_are_bound_and_reused(self):
        (self.case.repo/"dependencies/.ignored-input").write_bytes(b'z'*(9*1024*1024))
        self.prepare()
        self.assertEqual(self.action("evaluate")["state"],"CLAIMABLE")

    def test_final_capture_root_replacement_revocation_and_provenance_change_never_close(self):
        for change in ("root", "revoke", "provenance"):
            with self.subTest(change=change):
                case=ImmutableRootsTests();case.setUp()
                try:
                    case.prepare();original=snapshot.capture;calls=[]
                    def captured(*args,**kwargs):
                        calls.append(1)
                        raw=original(*args,**kwargs)
                        if len(calls)==3:
                            if change=="root":
                                root=case.case.repo/"dependencies"
                                root.rename(case.case.root/"previous-dependencies")
                                root.mkdir();(root/".ignored-input").write_bytes(b"changed")
                            elif change=="revoke":case.host.change={"revoked":True}
                            else:(case.case.repo/"build.json").write_text('{"changed":true}\n')
                        return raw
                    try:
                        with patch.object(snapshot,"capture",side_effect=captured):result=case.action("close")
                    except RuntimeError:
                        result=None
                    if result is not None:
                        self.assertNotEqual(result["state"],"CLOSED")
                        self.assertEqual(result["decision"],"NOT_CLAIMABLE")
                    self.assertFalse(any(json.loads(p.read_text())["kind"]=="CLAIM_CLOSED" for p in case.case.store.glob("[0-9]*.json")))
                finally:case.doCleanups()

    def test_inadequate_guard_capability_flags_and_subclass_cannot_grant_reuse(self):
        self.prepare();before={p.name:p.read_bytes() for p in self.case.store.iterdir()};original=self.host.protect
        self.host.manifest=replace(self.host.manifest,capabilities=("repository.immutable-root",))
        with self.assertRaisesRegex(RuntimeError,"immutable_dependency_guard_unavailable"):self.action("evaluate")
        self.host.manifest=TrustedTestHost.manifest
        touched=[]
        class PropertyState(ImmutableRootGuardState):
            @property
            def active(self):touched.append(1);return True
            @active.setter
            def active(self,value):pass
        for change in ({"protection_authenticated":False},{"protection_complete":False},{"state":PropertyState()}):
            with patch.object(self.host,"protect",side_effect=lambda request,at_ns:replace(original(request,at_ns),**change)):
                with self.assertRaisesRegex(RuntimeError,"immutable_dependency_guard_unavailable"):self.action("close")
        self.assertEqual(touched,[])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.case.store.iterdir()})

    def test_capture_interruption_releases_core_guard_mutex_without_external_callback(self):
        from threading import Thread
        self.prepare()
        with patch.object(snapshot,"capture",side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.action("evaluate")
        guard=self.host.guards[-1];acquired=[]
        def acquire():
            result=guard.mutex.acquire(timeout=.2);acquired.append(result)
            if result:guard.mutex.release()
        thread=Thread(target=acquire);thread.start();thread.join(.5)
        self.assertEqual(acquired,[True])
        self.assertEqual(self.action("evaluate")["state"],"CLAIMABLE")

    def test_guard_expiry_during_final_capture_blocks_without_reauthorization(self):
        self.prepare();original_protect=self.host.protect;original_capture=snapshot.capture;calls=[]
        def protect(request,at_ns):
            guard=original_protect(request,at_ns)
            return replace(guard,proof=replace(guard.proof,expires_ns=at_ns+2*10**9))
        def captured(*args,**kwargs):
            calls.append(1);raw=original_capture(*args,**kwargs)
            if len(calls)==3:time.sleep(2.05)
            return raw
        with patch.object(self.host,"protect",side_effect=protect),patch.object(snapshot,"capture",side_effect=captured):
            with self.assertRaisesRegex(RuntimeError,"immutable_root_proof_unknown"):self.action("close")
        self.assertFalse(any(json.loads(p.read_text())["kind"]=="CLAIM_CLOSED" for p in self.case.store.glob("[0-9]*.json")))

    def test_revoked_stale_incomplete_or_forged_proof_blocks_all_readonly_and_close_paths(self):
        self.prepare();before={p.name:p.read_bytes() for p in self.case.store.iterdir()}
        for change in ({"revoked":True},{"expires_ns":1},{"coverage_complete":False},{"immutable_verified":False},{"source_authenticated":False},{"provider_identity":"0"*64}):
            self.host.change=change
            with self.assertRaises(RuntimeError):self.action("evaluate")
            with self.assertRaises(RuntimeError):self.action("close")
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.case.store.iterdir()})

    def test_host_reports_external_hidden_modes_deletes_links_and_identity_changes(self):
        self.prepare();self.host.change={"revoked":True}
        root=self.case.repo/"dependencies";file=root/".ignored-input"
        file.write_bytes(b'b'*(2*1024*1024))
        with self.assertRaises(RuntimeError):self.action("close")
        file.chmod(0o755)
        with self.assertRaises(RuntimeError):self.action("close")
        file.unlink();(root/"escape").symlink_to(self.case.root/"outside")
        with self.assertRaises(RuntimeError):self.action("close")
        # No OS immutability is asserted: revocation is the trusted host result.

    def test_missing_json_provider_and_mutable_roots_cannot_grant_reuse(self):
        with self.assertRaisesRegex(RuntimeError,"immutable_dependency_proof_unavailable"):
            application.create(str(self.case.store),str(self.case.input),preset_config=str(self.case.config),
                integration_config=str(self.case.integration),dependency_provider={"success":True})
        self.case.value["authority"]["write"].append("dependencies")
        self.case.input.write_text(json.dumps(self.case.value))
        with self.assertRaisesRegex(RuntimeError,"immutable_root_mutable_or_unreadable_scope"):self.prepare()

    def test_signed_journal_proof_integer_boolean_does_not_grant_host_trust(self):
        import hashlib,hmac
        self.prepare();key=(self.case.store/"key").read_bytes();previous="0"*64
        for file in sorted(self.case.store.glob("[0-9]*.json")):
            event=json.loads(file.read_text())
            if event["kind"]=="BASELINE_CAPTURED":
                event["payload"]["snapshot"]["dependency_proofs"]["roots"]["dependencies"]["coverage_complete"]=1
            event["previous"]=previous;body={k:v for k,v in event.items() if k!="signature"}
            event["signature"]=hmac.new(key,snapshot.canonical(body),hashlib.sha256).hexdigest()
            file.write_bytes(snapshot.canonical(event));previous=hashlib.sha256(snapshot.canonical(event)).hexdigest()
        with self.assertRaisesRegex(RuntimeError,"immutable_root_proof_unknown"):self.action("resume")

    def test_initial_escaping_link_and_incomplete_capture_never_create_binding(self):
        (self.case.repo/"dependencies/escape").symlink_to(self.case.root/"outside")
        with self.assertRaisesRegex(RuntimeError,"symlink_unsupported"):self.prepare()
        self.assertFalse(self.case.store.exists())
        (self.case.repo/"dependencies/escape").unlink()
        self.case.policy["capture_budget"]["entries"]=1;self.case.integration.write_text(json.dumps(self.case.policy))
        with self.assertRaises(RuntimeError):self.prepare()
        self.assertFalse(self.case.store.exists())
