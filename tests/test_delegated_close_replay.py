"""Defensive semantic validation of signed local delegated-close history."""
import copy
import hashlib
import hmac
import json
import unittest
import test_integration_contract as fixture
from taskclosurekit import application
from taskclosurekit._primitives.snapshot import canonical
from taskclosurekit.storage.local_hmac import LocalHmacStore
from taskclosurekit.integration.adapters import AuthorityProvider,ProviderManifest,VerifiedCapability

class Issuer(AuthorityProvider):
    manifest=ProviderManifest("taskclosurekit/adapters/v1","c"*64,("authority.one-time",),("trusted-host-code",))
    def verify(self,selector,request,at_ns):
        return VerifiedCapability(request,(request.operation,),at_ns-1,at_ns+60*10**9,False,True,True,"once","d"*64)
    def consume(self,selector,capability):pass

class DelegatedCloseReplayTests(unittest.TestCase):
    def setUp(self):
        self.case=fixture.IntegrationContractTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.case.prepare();self.case.ready()
        result=application.execute(str(self.case.store),"close",authority_provider=Issuer(),capability_selector="grant")
        self.assertEqual(result["state"],"CLOSED")
        self.events=[json.loads(p.read_text()) for p in sorted(self.case.store.glob("[0-9]*.json"))]

    def rewrite(self,events,preserve_closure_sequence=False):
        key=(self.case.store/"key").read_bytes();previous="0"*64
        for p in self.case.store.glob("[0-9]*.json"):p.unlink()
        for sequence,event in enumerate(events,1):
            event["seq"]=sequence;event["previous"]=previous
            if event["kind"]=="CLAIM_CLOSED" and not preserve_closure_sequence:event["payload"]["closure"]["sequence"]=sequence
            body={k:v for k,v in event.items() if k!="signature"}
            event["signature"]=hmac.new(key,canonical(body),hashlib.sha256).hexdigest()
            target=self.case.store/(str(sequence).zfill(4)+".json")
            target.write_bytes(canonical(event));target.chmod(0o600)
            previous=hashlib.sha256(canonical(event)).hexdigest()

    def test_delegated_close_requires_started_consumed_and_true_audit(self):
        variants=("no_audits","no_started","no_consumed","no_audit","false_audit")
        for variant in variants:
            with self.subTest(variant=variant):
                events=copy.deepcopy(self.events)
                if variant=="no_audits":
                    events=[e for e in events if not e["kind"].startswith("CLOSE_CAPABILITY_")]
                    events[-1]["payload"].pop("consume_audit")
                elif variant in ("no_started","no_consumed"):
                    events=[e for e in events if e["kind"]!=("CLOSE_CAPABILITY_STARTED" if variant=="no_started" else "CLOSE_CAPABILITY_CONSUMED")]
                elif variant=="no_audit":events[-1]["payload"].pop("consume_audit")
                else:events[-1]["payload"]["consume_audit"]=False
                self.rewrite(events)
                self.assertEqual(len(LocalHmacStore(str(self.case.store)).read()),len(events))
                with self.assertRaisesRegex(RuntimeError,"unknown_or_missing_fields|invalid_capability_audit|invalid_state_sequence"):
                    self.case.action("resume")

    def test_valid_delegated_close_and_body_transaction_sequence_mismatch(self):
        self.assertEqual(self.case.action("resume")["state"],"CLOSED")
        for variant in ("authorization","transaction","binding","sequence"):
            with self.subTest(variant=variant):
                events=copy.deepcopy(self.events)
                if variant=="authorization":events[-1]["payload"]["authorization"]["capability"]["nonce"]="another"
                elif variant=="transaction":events[-2]["run_id"]="another-task"
                elif variant=="binding":events[-2]["payload"]["binding"]="0"*64
                else:events[-1]["payload"]["closure"]["sequence"]+=1
                self.rewrite(events,preserve_closure_sequence=variant=="sequence")
                if variant!="transaction":self.assertEqual(len(LocalHmacStore(str(self.case.store)).read()),len(events))
                with self.assertRaisesRegex(RuntimeError,"invalid_capability_audit|store_binding_mismatch|close_binding_mismatch"):
                    self.case.action("resume")
