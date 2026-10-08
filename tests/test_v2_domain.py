"""Pure v2 authority/evidence gates: no filesystem or CLI imports."""
import unittest
from dataclasses import replace
from taskclosurekit.domain.contract import parse_contract
from taskclosurekit.domain.evidence import Evidence, EvidenceSource
from taskclosurekit.domain.snapshot import Snapshot
from taskclosurekit.domain.review import Review
from taskclosurekit.engine.evaluate import evaluate


def contract():
    return parse_contract({"schema": "taskclosurekit/v2", "task": {"id": "sample", "title": "Sample"},
        "repository": "/tmp/sample", "authority": {"task": {"issuer": "human"},
        "read": ["README.md", "src"], "write": ["src/foo.py"],
        "execution": {"presets": ["git-index-whitespace-v1"]}}, "sources": ["README.md"],
        "acceptance": [{"id": "whitespace", "required": True,
                        "evidence": {"all_of": ["git-index-whitespace-v1"]}}],
        "review": {"required": True, "independence": "not_required"},
        "claim": {"type": "configured-acceptance-satisfied"}, "closure": {"authority": "human"}})


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.contract = contract()
        self.snapshot = Snapshot("1"*64, "2"*64, "3"*64, "4"*64, "a"*40)
        self.evidence = Evidence("check-4", "git-index-whitespace-v1", "measured_local",
            EvidenceSource("local-preset", "measured_local"), self.contract.digest, self.snapshot,
            ("whitespace",), "PASS", 4, 100)

    def assess(self, evidence=None, review=None, **kwargs):
        return evaluate(self.contract, self.snapshot, evidence or (self.evidence,), review,
                        authorized=True, baselined=True, **kwargs)

    def test_claimable_is_not_closed_and_has_limitations(self):
        preliminary = self.assess()
        review = Review(self.contract.digest, self.snapshot.digest, preliminary.evidence_set,
                        "approve", EvidenceSource("local-operator", "human_confirmed"), "NOT_REQUIRED", 5)
        result = self.assess(review=review)
        self.assertEqual(result.state, "CLAIMABLE")
        self.assertEqual(result.decision, "CLAIMABLE")
        self.assertTrue(result.claim.limitations)

    def test_agent_assertion_cannot_satisfy_measured_criterion(self):
        assertion = replace(self.evidence, trust_class="agent_attested",
                            source=EvidenceSource("agent-import", "agent_attested"))
        result = self.assess((assertion,))
        self.assertEqual(result.decision, "NOT_CLAIMABLE")
        self.assertIn("missing_required_evidence", result.reasons)

    def test_mismatched_source_cannot_escalate_trust(self):
        forged = replace(self.evidence, source=EvidenceSource("agent-import", "agent_attested"))
        self.assertIn("invalid_evidence_source", self.assess((forged,)).reasons)

    def test_stale_authority_environment_and_input_block(self):
        for field, reason in [("authority", "STALE_AUTHORITY"), ("execution", "STALE_ENVIRONMENT"),
                              ("repository", "STALE_INPUT")]:
            with self.subTest(field=field):
                old = replace(self.snapshot, **{field: "f"*64})
                result = self.assess((replace(self.evidence, snapshot=old),))
                self.assertEqual(result.freshness[0][1], reason)
                self.assertEqual(result.decision, "NOT_CLAIMABLE")

    def test_latest_failed_evidence_does_not_resurrect_pass(self):
        failure = replace(self.evidence, id="check-7", sequence=7, result="FAIL")
        self.assertIn("required_evidence_failed", self.assess((self.evidence, failure)).reasons)

    def test_unknown_independence_blocks(self):
        required = replace(self.contract, review_independence="required")
        preliminary = self.assess()
        review = Review(required.digest, self.snapshot.digest, preliminary.evidence_set,
                        "approve", EvidenceSource("agent-import", "agent_attested"), "UNKNOWN", 5)
        result = evaluate(required, self.snapshot, (self.evidence,), review, authorized=True, baselined=True)
        self.assertEqual(result.independence, "UNKNOWN")
        self.assertIn("independence_unknown", result.reasons)

    def test_unknown_state_and_scope_fail_closed(self):
        self.assertIn("write_scope_violation", self.assess(authority_error="write_scope_violation").reasons)
        self.assertIn("unknown_state", self.assess(known_state=False).reasons)

    def test_closure_rejects_integer_operator_confirmation_booleans(self):
        from taskclosurekit.engine.closure import action_binding, close
        preliminary=self.assess()
        review=Review(self.contract.digest,self.snapshot.digest,preliminary.evidence_set,
            "approve",EvidenceSource("local-operator","human_confirmed"),"NOT_REQUIRED",5)
        assessed=self.assess(review=review)
        binding=action_binding("close",self.contract.digest,self.snapshot.digest,assessed.evidence_set)
        receipt={"source":"local-operator","binding":binding,
                 "host_operator_assumed":True,"identity_verified":False}
        for field,value in (("host_operator_assumed",1),("identity_verified",0)):
            with self.subTest(field=field):
                invalid={**receipt,field:value}
                with self.assertRaisesRegex(RuntimeError,"closure_authority_unconfirmed"):
                    close(assessed,invalid,6)

    def test_contract_cannot_define_commands_or_unknown_presets(self):
        from dataclasses import asdict
        self.assertEqual(self.contract.claim_type, "configured-acceptance-satisfied")
        from taskclosurekit.execution.presets import REGISTRY
        preset = REGISTRY.get("git-index-whitespace-v1")
        self.assertEqual(preset.argv[1], "diff")
        with self.assertRaises(RuntimeError):
            REGISTRY.get("shell:any")
