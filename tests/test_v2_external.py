import unittest
from dataclasses import replace
from taskclosurekit.domain.contract import parse_contract
from taskclosurekit.domain.snapshot import Snapshot, CommitSnapshotBinding
from taskclosurekit.trust.ci import CIAdapter, VerifiedCIResult, accept_ci
from test_v2_domain import contract

class FakeCI(CIAdapter):
    trusted_workflow_identity = "b"*64
    adapter_identity = "c"*64
    selected_checks = {"whitespace": "git-index-whitespace-v1"}

    def verify(self, selector):
        return self.result

class CISeamTests(unittest.TestCase):
    def setUp(self):
        self.contract=contract();self.snapshot=Snapshot("1"*64,"2"*64,"3"*64,"4"*64,"a"*40)
        self.adapter=FakeCI()
        self.adapter.result=VerifiedCIResult(self.contract.repository,self.snapshot.commit,"b"*64,
            "whitespace","git-index-whitespace-v1",("whitespace",),"PASS","d"*64)

    def accept(self):
        return accept_ci(self.adapter,"opaque-selector",self.contract,self.snapshot,5,100,
                         commit_binding=CommitSnapshotBinding(self.contract.repository,self.snapshot.commit,self.snapshot.digest))

    def test_fully_bound_fake_adapter_creates_measured_ci(self):
        evidence,verification=self.accept()
        self.assertEqual(evidence.trust_class,"measured_ci")
        self.assertEqual(evidence.snapshot,self.snapshot)
        self.assertEqual(verification["workflow_identity"],"b"*64)
        from taskclosurekit.engine.evaluate import evaluate
        assessed=evaluate(self.contract,self.snapshot,(evidence,),authorized=True,baselined=True)
        self.assertNotIn("missing_required_evidence",assessed.reasons)
        self.assertIn("missing_trusted_review",assessed.reasons)

    def test_commit_to_snapshot_correspondence_is_mandatory(self):
        with self.assertRaisesRegex(RuntimeError,"ci_snapshot_not_commit_bound"):
            accept_ci(self.adapter,"selector",self.contract,self.snapshot,5,100)

    def test_every_provider_binding_is_required(self):
        for field,value in [("repository","/tmp/other"),("commit","e"*40),("workflow_identity","f"*64),
            ("selected_check","untrusted"),("preset_id","shell"),("criteria",("other",)),("result","FAIL"),
            ("artifact_digest","wrong")]:
            with self.subTest(field=field):
                initial=self.adapter.result
                self.adapter.result=replace(initial,**{field:value})
                with self.assertRaises(RuntimeError):self.accept()
                self.adapter.result=initial

    def test_unconfigured_or_json_adapter_cannot_supply_trust(self):
        with self.assertRaises(RuntimeError):accept_ci({},"selector",self.contract,self.snapshot,5,100)
