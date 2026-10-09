"""V2 vertical slices on disposable Git repositories; local operator is explicit."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from taskclosurekit import application
from taskclosurekit.trust.local_operator import LocalOperator

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")

class ConfirmedOperator(LocalOperator):
    """Test trusted-host adapter, no claim of independently proven human identity."""
    def confirm(self, operation, binding):
        return {"source":"local-operator", "binding":binding,
                "host_operator_assumed":True, "identity_verified":False}

@unittest.skipUnless(GIT, "Git required")
class V2CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root/"repo"; self.repo.mkdir()
        self.store = self.root/"store"; self.input = self.root/"contract.json"
        self.env = {"PATH":os.environ.get("PATH",""),"LC_ALL":"C", "GIT_CONFIG_NOSYSTEM":"1",
                    "GIT_CONFIG_GLOBAL":os.devnull, "GIT_AUTHOR_NAME":"Fixture",
                    "GIT_AUTHOR_EMAIL":"fixture@example.invalid", "GIT_COMMITTER_NAME":"Fixture",
                    "GIT_COMMITTER_EMAIL":"fixture@example.invalid","PYTHONDONTWRITEBYTECODE":"1"}
        self.git("init", "--template=", "--initial-branch=master")
        (self.repo/"README.md").write_text("Authority fixture.\n")
        (self.repo/"src").mkdir(); (self.repo/"src/foo.py").write_text("value = 1\n")
        self.git("add", ".");self.git("commit", "-m", "baseline")
        self.value = {"schema":"taskclosurekit/v2", "task":{"id":"test-task","title":"Test task"},
            "repository":str(self.repo), "authority":{"task":{"issuer":"human"},
            "read":["README.md","src"],"write":["src/foo.py"],
            "execution":{"presets":["git-index-whitespace-v1"]}},"sources":["README.md"],
            "acceptance":[{"id":"staged-whitespace","required":True,
                          "evidence":{"all_of":["git-index-whitespace-v1"]}}],
            "review":{"required":True,"independence":"not_required"},
            "claim":{"type":"configured-acceptance-satisfied"},"closure":{"authority":"human"}}
        self.input.write_text(json.dumps(self.value))
        self.operator = ConfirmedOperator()

    def git(self,*args):
        return subprocess.run([GIT,*args],cwd=self.repo,env=self.env,capture_output=True,check=True).stdout

    def run_action(self, action, **kw):
        return application.execute(str(self.store), action, operator=kw.pop("operator",self.operator), **kw)

    def prepare(self):
        created = application.create(str(self.store),str(self.input))
        self.assertEqual(created["state"],"DRAFT")
        authorized = self.run_action("authorize")
        self.assertEqual(authorized["state"],"AUTHORIZED")
        self.assertEqual(self.run_action("baseline")["state"],"BASELINED")

    def check(self):
        return self.run_action("check",preset_id="git-index-whitespace-v1")

    def test_happy_stale_recheck_review_claimable_explicit_close(self):
        self.prepare()
        (self.repo/"src/foo.py").write_text("value = 2\n");self.git("add","src/foo.py")
        checked=self.check();self.assertEqual(checked["state"],"EVIDENCED")
        self.assertEqual(checked["evidence"][0]["trust_class"],"measured_local")
        self.assertEqual(checked["evidence"][0]["snapshot"]["digest"],checked["snapshot"])
        (self.repo/"src/foo.py").write_text("value = 3\n")
        stale=self.run_action("evaluate");self.assertIn("stale_evidence",stale["reasons"])
        self.assertEqual(stale["freshness"][0]["state"],"STALE_INPUT")
        self.git("add","src/foo.py");self.check()
        reviewed=self.run_action("review",human=True)
        self.assertEqual(reviewed["state"],"REVIEWED")
        before=list(self.store.iterdir())
        evaluated=self.run_action("evaluate")
        self.assertEqual(evaluated["decision"],"CLAIMABLE")
        self.assertEqual(evaluated["state"],"CLAIMABLE")
        self.assertEqual(before,list(self.store.iterdir()))
        closed=self.run_action("close")
        self.assertEqual(closed["state"],"CLOSED")
        self.assertEqual(closed["claim"]["type"],"configured-acceptance-satisfied")
        self.assertTrue(closed["claim"]["limitations"])
        self.assertFalse(closed["closure"]["identity_verified"])

    def test_scope_violation_blocks_even_with_pass(self):
        self.prepare();self.check()
        (self.repo/"other.md").write_text("Out of scope.\n")
        result=self.run_action("evaluate")
        self.assertIn("write_scope_violation",result["reasons"])
        self.assertEqual(self.run_action("close")["decision"],"NOT_CLAIMABLE")

    def test_exact_requested_scope_slice_foo_and_readme(self):
        self.prepare();self.check()
        (self.repo/"src/foo.py").write_text("value = 2\n")
        (self.repo/"README.md").write_text("Changed outside write scope.\n")
        self.git("add","src/foo.py","README.md")
        result=self.run_action("evaluate")
        self.assertIn("write_scope_violation",result["reasons"])

    def test_confirmation_race_never_closes_changed_snapshot(self):
        self.prepare();self.check();self.run_action("review",human=True)
        target=self.repo/"src/foo.py"
        class RacingOperator(ConfirmedOperator):
            def confirm(self,operation,binding):
                target.write_text("value = 9\n")
                return super().confirm(operation,binding)
        before=list(self.store.glob("[0-9]*.json"))
        with self.assertRaisesRegex(RuntimeError,"inputs_changed_during_confirmation"):
            self.run_action("close",operator=RacingOperator())
        self.assertEqual(before,list(self.store.glob("[0-9]*.json")))

    def test_commit_binding_refuses_dirty_worktree(self):
        self.prepare()
        from taskclosurekit.domain.contract import parse_contract
        from taskclosurekit.snapshots.repository import bind_commit
        contract=parse_contract(self.value)
        binding=bind_commit(contract,str(self.input))
        self.assertEqual(binding.commit,self.git("rev-parse","HEAD").decode().strip())
        (self.repo/"src/foo.py").write_text("value = 2\n")
        with self.assertRaisesRegex(RuntimeError,"ci_snapshot_not_commit_bound"):
            bind_commit(contract,str(self.input))

    def test_required_independence_with_present_agent_review_is_unknown(self):
        self.value["review"]["independence"]="required";self.input.write_text(json.dumps(self.value))
        self.prepare();self.check()
        assessed=self.run_action("evaluate")
        review=self.root/"review.json"
        review.write_text(json.dumps({"schema":"taskclosurekit/review/v2","task_id":"test-task",
            "contract_digest":assessed["contract_digest"],"snapshot_digest":assessed["snapshot"],
            "evidence_set":assessed["evidence_set"],"verdict":"approve"}))
        self.run_action("review",input_path=str(review))
        result=self.run_action("evaluate")
        self.assertTrue(result["review_present"])
        self.assertEqual(result["independence"],"UNKNOWN")
        self.assertIn("independence_unknown",result["reasons"])

    def test_noninteractive_operator_cannot_authorize_or_close(self):
        application.create(str(self.store),str(self.input))
        result=self.run_action("authorize",operator=LocalOperator())
        self.assertIn("human_confirmation_unavailable",result["reasons"])
        self.assertEqual(len(list(self.store.glob("[0-9]*.json"))),1)

    def test_review_rerun_and_snapshot_bindings(self):
        self.prepare();self.check();self.run_action("review",human=True)
        self.assertEqual(self.run_action("evaluate")["decision"],"CLAIMABLE")
        self.check()
        self.assertIn("stale_review",self.run_action("evaluate")["reasons"])

    def test_authority_drift_blocks(self):
        self.prepare();self.check();self.run_action("review",human=True)
        self.input.write_text(self.input.read_text()+" ")
        self.assertIn("authority_changed",self.run_action("evaluate")["reasons"])

    def test_agent_review_cannot_supply_human_trust_fields(self):
        self.prepare();self.check()
        review=self.root/"review.json"
        review.write_text(json.dumps({"schema":"taskclosurekit/review/v2","issuer":"human", "trust_class":"human_confirmed"}))
        with self.assertRaises(RuntimeError):self.run_action("review",input_path=str(review))

    def test_unknown_and_tampered_records_fail_closed(self):
        self.prepare()
        path=self.store/"0002.json"; data=json.loads(path.read_text());data["kind"]="CLAIM_CLOSED"
        path.write_text(json.dumps(data));path.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError,"evidence_tampered"): self.run_action("status")

    def cli(self, *args, human=False):
        command=[sys.executable,"-m","taskclosurekit","--store",str(self.store),*args,"--json"]
        if not human:
            return subprocess.run(command,cwd=ROOT,env=self.env,capture_output=True,text=True,timeout=15)
        import pty
        import re
        import select
        master,slave=pty.openpty()
        try:
            process=subprocess.Popen(command,cwd=ROOT,env=self.env,stdin=slave,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            os.close(slave);slave=None
            ready,_,_=select.select([process.stderr],[],[],10)
            self.assertTrue(ready,"confirmation prompt was not reached")
            prompt=process.stderr.readline()
            self.assertIn("identity not verified",prompt)
            match=re.search(r"([0-9a-f]{64})\n$",prompt)
            self.assertIsNotNone(match,prompt)
            os.write(master,(match.group(1)+"\n").encode())
            stdout,stderr=process.communicate(timeout=15)
            return subprocess.CompletedProcess(command,process.returncode,stdout,prompt+stderr)
        finally:
            if slave is not None:os.close(slave)
            os.close(master)

    def test_public_cli_full_flow_and_cross_process_handoff(self):
        self.assertEqual(self.cli("task","create",str(self.input)).returncode,0)
        self.assertEqual(self.cli("authorize",human=True).returncode,0)
        self.assertEqual(self.cli("baseline").returncode,0)
        (self.repo/"src/foo.py").write_text("value = 5\n");self.git("add","src/foo.py")
        self.assertEqual(self.cli("check","git-index-whitespace-v1").returncode,0)
        before={p.name:p.read_bytes() for p in self.store.iterdir()}
        handoff=json.loads(self.cli("status","--next").stdout)
        self.assertEqual(handoff["last_confirmed"],"EVIDENCED")
        self.assertEqual(handoff["evidence"][0]["result"],"PASS")
        self.assertEqual(handoff["next_action"],"review")
        self.assertIn("missing_trusted_review",handoff["reasons"])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.store.iterdir()})
        self.assertEqual(self.cli("review","--human",human=True).returncode,0)
        evaluated=json.loads(self.cli("evaluate").stdout)
        self.assertEqual(evaluated["state"],"CLAIMABLE")
        self.assertEqual(self.cli("close").returncode,1)
        self.assertEqual(self.cli("check","git-index-whitespace-v1").returncode,0)
        self.assertIn("stale_review",json.loads(self.cli("evaluate").stdout)["reasons"])
        self.assertEqual(self.cli("review","--human",human=True).returncode,0)
        closed=json.loads(self.cli("close",human=True).stdout)
        self.assertEqual(closed["state"],"CLOSED")
        self.assertEqual(closed["claim"]["snapshot_digest"],closed["snapshot"])

    def test_new_process_handoff_lists_stale_evidence(self):
        self.prepare();self.check()
        (self.repo/"src/foo.py").write_text("value = 8\n")
        result=json.loads(self.cli("resume").stdout)
        self.assertEqual(result["freshness"][0]["state"],"STALE_INPUT")
        self.assertEqual(result["next_action"],"check")
        self.assertEqual(result["last_confirmed"],"EVIDENCED")

    def test_signed_unknown_event_sequence_rejected(self):
        self.prepare()
        from taskclosurekit.storage.local_hmac import LocalHmacStore
        store=LocalHmacStore(str(self.store))
        with store.locked():
            events=store.read();store.append(events,"MAGIC_COMPLETION",{},"test-task")
        with self.assertRaisesRegex(RuntimeError,"invalid_state_sequence"):self.run_action("evaluate")

    def test_assertions_are_separate_and_cannot_escalate_or_satisfy_acceptance(self):
        self.prepare()
        assertion=self.root/"assertion.json"
        assertion.write_text(json.dumps({"schema":"taskclosurekit/assertion/v2","task_id":"test-task",
                                         "statement":"Synthetic claim contains no measured proof."}))
        recorded=self.cli("attest",str(assertion))
        self.assertEqual(recorded.returncode,0,recorded.stdout)
        status=json.loads(self.cli("status").stdout)
        self.assertEqual(status["assertions"][0]["source"]["trust_class"],"agent_attested")
        self.assertNotIn("statement",status["assertions"][0])
        self.assertEqual(status["evidence"],[])
        self.assertIn("missing_required_evidence",status["reasons"])
        for name,value in [("trust_class","measured_local"),("issuer","human"),("source","local-preset"),("criteria",["staged-whitespace"])]:
            data=json.loads(assertion.read_text());data[name]=value;assertion.write_text(json.dumps(data))
            invalid=self.cli("attest",str(assertion))
            self.assertEqual(invalid.returncode,2)
            del data[name];assertion.write_text(json.dumps(data))

    def test_output_projection_cannot_change_journal_trust(self):
        self.prepare();self.check()
        result=self.run_action("status")
        result["evidence"][0]["source"]={"id":"local-operator","trust_class":"human_confirmed"}
        result["evidence"][0]["trust_class"]="human_confirmed"
        result["decision"]="CLAIMABLE";result["state"]="CLOSED"
        current=self.run_action("status")
        self.assertEqual(current["evidence"][0]["source"]["id"],"local-preset")
        self.assertEqual(current["evidence"][0]["trust_class"],"measured_local")
        self.assertEqual(current["decision"],"NOT_CLAIMABLE")

    def test_signed_malformed_receipt_is_semantically_invalid(self):
        self.prepare();self.check()
        import hashlib
        import hmac
        from taskclosurekit._primitives.snapshot import canonical
        path=self.store/"0005.json"
        event=json.loads(path.read_text())
        event["payload"]["evidence"]["trust_class"]="human_confirmed"
        body={k:v for k,v in event.items() if k!="signature"}
        event["signature"]=hmac.new((self.store/"key").read_bytes(),canonical(body),hashlib.sha256).hexdigest()
        path.write_bytes(canonical(event));path.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError,"receipt_binding_mismatch"):
            self.run_action("evaluate")

    def test_signed_confirmation_integer_booleans_rejected_for_every_authority_event(self):
        self.prepare();self.check();self.run_action("review",human=True);self.run_action("close")
        import hashlib
        import hmac
        from taskclosurekit._primitives.snapshot import canonical
        from taskclosurekit.storage.local_hmac import LocalHmacStore
        store=LocalHmacStore(str(self.store))
        with store.locked(readonly=True):
            originals=store.read()
        key=(self.store/"key").read_bytes()
        for kind in ("AUTHORITY_VALIDATED","REVIEW_RECORDED","CLAIM_CLOSED"):
            for field,value in (("host_operator_assumed",1),("identity_verified",0)):
                with self.subTest(kind=kind,field=field):
                    events=json.loads(json.dumps(originals))
                    target=next(event for event in events if event["kind"]==kind)
                    target["payload"]["confirmation"][field]=value
                    previous="0"*64
                    for event in events:
                        event["previous"]=previous
                        body={k:v for k,v in event.items() if k!="signature"}
                        event["signature"]=hmac.new(key,canonical(body),hashlib.sha256).hexdigest()
                        path=self.store/("%04d.json"%event["seq"])
                        path.write_bytes(canonical(event));path.chmod(0o600)
                        previous=hashlib.sha256(canonical(event)).hexdigest()
                    # Transport integrity is valid; semantic validation must still reject.
                    with store.locked(readonly=True):
                        self.assertEqual(len(store.read()),len(originals))
                    with self.assertRaisesRegex(RuntimeError,"invalid_authority_confirmation"):
                        self.run_action("status")

    def test_signed_assertion_and_closure_sequences_require_positive_integers(self):
        self.prepare()
        assertion=self.root/"assertion.json"
        assertion.write_text(json.dumps({"schema":"taskclosurekit/assertion/v2","task_id":"test-task",
                                         "statement":"Sequence validation fixture."}))
        self.run_action("attest",input_path=str(assertion))
        self.check();self.run_action("review",human=True);self.run_action("close")
        import hashlib
        import hmac
        from taskclosurekit._primitives.snapshot import canonical
        from taskclosurekit.storage.local_hmac import LocalHmacStore
        store=LocalHmacStore(str(self.store))
        with store.locked(readonly=True):originals=store.read()
        key=(self.store/"key").read_bytes()
        for kind,field in (("AGENT_ASSERTION_RECORDED","assertion"),("CLAIM_CLOSED","closure")):
            sequence=next(event["seq"] for event in originals if event["kind"]==kind)
            for invalid in (float(sequence),True,0,-1):
                with self.subTest(kind=kind,value=invalid):
                    events=json.loads(json.dumps(originals))
                    target=next(event for event in events if event["kind"]==kind)
                    target["payload"][field]["sequence"]=invalid
                    previous="0"*64
                    for event in events:
                        event["previous"]=previous
                        body={k:v for k,v in event.items() if k!="signature"}
                        event["signature"]=hmac.new(key,canonical(body),hashlib.sha256).hexdigest()
                        path=self.store/("%04d.json"%event["seq"])
                        path.write_bytes(canonical(event));path.chmod(0o600)
                        previous=hashlib.sha256(canonical(event)).hexdigest()
                    with store.locked(readonly=True):
                        self.assertEqual(len(store.read()),len(originals))
                    with self.assertRaises(RuntimeError):self.run_action("status")

    def test_cli_json_invalid_envelope_and_legacy_alias(self):
        p=subprocess.run([sys.executable,"-m","taskclosurekit","--store",str(self.store),"wat","--json"],
                         cwd=ROOT,env=self.env,capture_output=True,text=True)
        result=json.loads(p.stdout)
        self.assertEqual(p.returncode,2)
        self.assertEqual(result["operational"]["status"],"invalid_input")
        self.assertEqual(result["schema"],"taskclosurekit/result/v2")
