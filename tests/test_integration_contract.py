"""Integration policy through the real application lifecycle, disposable Python toolchain."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from taskclosurekit import application
from test_v2_cycle import ConfirmedOperator

class IntegrationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.repo = self.root / "repo"; self.repo.mkdir()
        self.store = self.root / "store"; self.input = self.root / "contract.json"
        self.config = self.root / "presets.json"; self.integration = self.root / "integration.json"
        self.env = {"PATH":"/usr/bin:/bin", "LC_ALL":"C", "GIT_CONFIG_NOSYSTEM":"1", "GIT_CONFIG_GLOBAL":os.devnull,
            "GIT_AUTHOR_NAME":"Fixture", "GIT_AUTHOR_EMAIL":"fixture@example.invalid", "GIT_COMMITTER_NAME":"Fixture", "GIT_COMMITTER_EMAIL":"fixture@example.invalid"}
        self.git("init", "--template=", "--initial-branch=master")
        (self.repo / "README.md").write_text("Task authority.\n")
        (self.repo / "input.txt").write_text("valid\n"); (self.repo / "build.json").write_text("{}\n")
        (self.repo / "check.py").write_text("from pathlib import Path\nimport sys\nassert Path('input.txt').read_text() == 'valid\\n'\nif len(sys.argv)>1:\n Path('generated').mkdir(exist_ok=True)\n Path('generated/result').write_text('built\\n')\n")
        self.git("add", "."); self.git("commit", "-m", "baseline")
        presets = []
        for name, argv, writes in [("test",["-B","check.py"],[]),("build",["-B","check.py","build"],["generated"])]:
            presets.append({"id":name,"executable":str(Path(sys.executable).resolve()),"argv":argv,"cwd_rule":"contract-repository",
                "environment_allowlist":{"PATH":"/usr/bin:/bin","PYTHONDONTWRITEBYTECODE":"1"},"timeout":5,"output_limit":65536,
                "permitted_writes":writes,"relevant_inputs":{"source":["input.txt"],"tests":[],"manifests":[],"lockfiles":[],"config":["build.json"]},"authority_inputs":["check.py"],"runtime_inputs":[]})
        self.config.write_text(json.dumps({"schema":"taskclosurekit/presets/v1","presets":presets}))
        self.value = {"schema":"taskclosurekit/v2","task":{"id":"integration","title":"Neutral integration"},"repository":str(self.repo),
            "authority":{"task":{"issuer":"human"},"read":["README.md","input.txt","build.json","check.py"],"write":["input.txt","build.json","generated"],"execution":{"presets":["test","build"]}},
            "sources":["README.md"],"acceptance":[{"id":"checks","required":True,"evidence":{"all_of":["test","build"]}}],
            "review":{"required":True,"independence":"not_required"},"claim":{"type":"configured-acceptance-satisfied"},"closure":{"authority":"human"}}
        self.input.write_text(json.dumps(self.value))
        self.policy = {"schema":"taskclosurekit/integration/v1","stage_equivalence":"content","roots":[
            {"path":"generated","type":"generated","acceptance":"exclude","rationale":"Build outputs are not read by either configured check.","dependency_provenance":None}],
            "coverage":"bounded-full-rehash"}
        self.integration.write_text(json.dumps(self.policy))
    def git(self,*args):
        return subprocess.run([shutil.which("git"),*args],cwd=self.repo,env=self.env,capture_output=True,check=True).stdout
    def action(self, op, **kwargs):
        return application.execute(str(self.store),op,operator=ConfirmedOperator(),**kwargs)
    def prepare(self):
        application.create(str(self.store),str(self.input),preset_config=str(self.config),integration_config=str(self.integration))
        self.action("authorize"); self.action("baseline")
    def ready(self):
        self.action("check",preset_id="test"); self.action("check",preset_id="build"); self.action("review",human=True)
    def test_generated_output_does_not_stale_unaffected_tests(self):
        self.prepare(); self.ready()
        self.assertEqual(self.action("evaluate")["state"],"CLAIMABLE")
        self.assertEqual(self.action("close")["state"],"CLOSED")

    def test_content_equivalent_commit_preserves_evidence_and_binds_final_sha(self):
        self.prepare()
        (self.repo / "build.json").write_text('{"strict":true}\n')
        self.git("add", "build.json")
        self.ready()
        before = self.action("evaluate")
        self.assertEqual(before["state"], "CLAIMABLE")
        self.git("commit", "-m", "reviewed content")
        after = self.action("evaluate")
        self.assertEqual(after["state"], "CLAIMABLE")
        self.assertEqual(after["evidence"], before["evidence"])
        self.assertEqual(after["result_identity"]["commit"], self.git("rev-parse", "HEAD").decode().strip())
        self.assertEqual(after["result_identity"]["stage"], "committed")

    def test_precommit_tests_transfer_to_equivalent_staged_review(self):
        self.prepare()
        (self.repo / "build.json").write_text('{"strict":true}\n')
        self.ready()
        before=self.action("evaluate")
        self.git("add","build.json")
        staged=self.action("evaluate")
        self.assertTrue(all(item["state"]=="CURRENT" for item in staged["freshness"]))
        self.assertIn("stale_review",staged["reasons"])
        self.action("review",human=True)
        self.assertEqual(self.action("evaluate")["state"],"CLAIMABLE")
        self.assertEqual(self.action("evaluate")["evidence"],before["evidence"])

    def test_required_independence_needs_verified_host_context_every_time(self):
        from taskclosurekit.integration.adapters import HostReviewProvider, ProviderManifest, VerifiedReview
        self.value["review"]["independence"] = "required"
        self.input.write_text(json.dumps(self.value))
        class Host(HostReviewProvider):
            manifest = ProviderManifest("taskclosurekit/adapters/v1", "a"*64, ("review.context",), ("trusted-host-code",))
            def verify(self, selector, request):
                return VerifiedReview(request, "reviewer", "session-r", ("creator",), ("session-c",),
                    True, True, "b"*64, "approve")
        self.prepare(); self.action("check",preset_id="test"); self.action("check",preset_id="build")
        self.action("review", review_provider=Host(), provider_selector="opaque-run")
        self.assertEqual(self.action("evaluate", review_provider=Host())["state"], "CLAIMABLE")
        absent = self.action("evaluate")
        self.assertIn("independence_unknown", absent["reasons"])
        self.assertEqual(self.action("close")["decision"], "NOT_CLAIMABLE")
        self.assertEqual(self.action("close", review_provider=Host())["state"], "CLOSED")

    def test_close_capability_is_external_bound_one_time_and_separate_from_operator(self):
        from taskclosurekit.integration.adapters import AuthorityProvider, ProviderManifest, VerifiedCapability
        class Issuer(AuthorityProvider):
            manifest = ProviderManifest("taskclosurekit/adapters/v1", "c"*64, ("authority.one-time",), ("trusted-host-code",))
            used = False
            def verify(self, selector, request, at_ns):
                return VerifiedCapability(request,(request.operation,),at_ns-1,at_ns+10**9,False,True,True,"once","d"*64)
            def consume(self, selector, capability):
                if self.used: raise RuntimeError("capability_replayed")
                self.used=True
        self.prepare();self.ready();issuer=Issuer()
        closed=application.execute(str(self.store),"close",authority_provider=issuer,capability_selector="external-grant")
        self.assertTrue(issuer.used)
        self.assertEqual(closed["closure"]["authority_source"],"delegated-authority")
        self.assertEqual(closed["state"],"CLOSED")
        self.assertEqual(application.execute(str(self.store),"close",authority_provider=issuer,capability_selector="external-grant")["reasons"],["closed_requires_new_task"])

    def test_consumed_close_callback_write_or_interruption_never_closes_or_reconsumes(self):
        from taskclosurekit.integration.adapters import AuthorityProvider, ProviderManifest, VerifiedCapability
        case=self
        class Issuer(AuthorityProvider):
            manifest=ProviderManifest("taskclosurekit/adapters/v1","c"*64,("authority.one-time",),("trusted-host-code",))
            consumed=0
            def verify(self,selector,request,at_ns):
                return VerifiedCapability(request,(request.operation,),at_ns-1,at_ns+60*10**9,False,True,True,"once","d"*64)
            def consume(self,selector,capability):
                self.consumed+=1
                (case.repo/"input.txt").write_text("invalid\n")
        self.prepare();self.ready();issuer=Issuer()
        refused=application.execute(str(self.store),"close",authority_provider=issuer,capability_selector="grant")
        self.assertEqual(refused["decision"],"NOT_CLAIMABLE")
        self.assertNotEqual(refused["state"],"CLOSED")
        (self.repo/"input.txt").write_text("valid\n")
        resumed=self.action("resume")
        self.assertIn("capability_attempt_requires_recovery",resumed["reasons"])
        application.execute(str(self.store),"close",authority_provider=issuer,capability_selector="grant")
        self.assertEqual(issuer.consumed,1)

    def test_diagnostics_redact_complete_quoted_escaped_and_truncated_values(self):
        script=self.repo/"check.py"
        script.write_text(script.read_text()+"\nimport json\nprint('password=\"synthetic public marker\"')\nprint(json.dumps({'token': 'escaped \"private tail\" end'}))\nprint('secret=\"truncated private tail'+'x'*200)\n")
        config=json.loads(self.config.read_text());config["schema"]="taskclosurekit/presets/v2"
        config["execution_policy"]={"schema":"taskclosurekit/execution-policy/v1","diagnostic_limit":160}
        self.config.write_text(json.dumps(config));self.prepare();self.action("check",preset_id="test")
        event=json.loads(sorted(self.store.glob("[0-9]*.json"))[-1].read_text())
        diagnostics=event["payload"]["journal"]["diagnostics"]
        serialized=json.dumps(diagnostics)
        for marker in ("public marker","private tail","truncated private"):
            self.assertNotIn(marker,serialized)
        self.assertTrue(diagnostics["redacted"])

    def test_pending_interrupted_consume_is_durable_and_never_retried(self):
        from taskclosurekit.integration.adapters import AuthorityProvider,ProviderManifest,VerifiedCapability
        class Issuer(AuthorityProvider):
            manifest=ProviderManifest("taskclosurekit/adapters/v1","c"*64,("authority.one-time",),("trusted-host-code",))
            calls=0
            def verify(self,selector,request,at_ns):return VerifiedCapability(request,(request.operation,),at_ns-1,at_ns+60*10**9,False,True,True,"once","d"*64)
            def consume(self,selector,capability):
                self.calls+=1
                raise KeyboardInterrupt
        self.prepare();self.ready();issuer=Issuer()
        with self.assertRaises(KeyboardInterrupt):application.execute(str(self.store),"close",authority_provider=issuer,capability_selector="grant")
        self.assertIn("capability_attempt_requires_recovery",self.action("resume")["reasons"])
        application.execute(str(self.store),"close",authority_provider=issuer,capability_selector="grant")
        self.assertEqual(issuer.calls,1)

    def test_consumed_capability_expiring_during_callback_is_refused_and_audited(self):
        from taskclosurekit.integration.adapters import AuthorityProvider,ProviderManifest,VerifiedCapability
        import time
        class Issuer(AuthorityProvider):
            manifest=ProviderManifest("taskclosurekit/adapters/v1","c"*64,("authority.one-time",),("trusted-host-code",))
            def verify(self,selector,request,at_ns):return VerifiedCapability(request,(request.operation,),at_ns-1,at_ns+500000000,False,True,True,"once","d"*64)
            def consume(self,selector,capability):time.sleep(.6)
        self.prepare();self.ready()
        refused=application.execute(str(self.store),"close",authority_provider=Issuer(),capability_selector="grant")
        self.assertEqual(refused["decision"],"NOT_CLAIMABLE")
        self.assertIn("capability_attempt_requires_recovery",self.action("resume")["reasons"])

    def test_trusted_capture_budget_supports_nine_megabyte_generated_output(self):
        self.policy.update(schema="taskclosurekit/integration/v2",coverage="bounded-host-proof-or-rehash",
            capture_budget={"file_bytes":32*1024*1024,"aggregate_bytes":256*1024*1024,"entries":20000,"seconds":30})
        self.integration.write_text(json.dumps(self.policy))
        script=self.repo/"check.py"
        script.write_text(script.read_text()+"\nif len(sys.argv)>1:\n Path('generated/large').write_bytes(b'x'*(9*1024*1024))\n")
        config=json.loads(self.config.read_text());config["schema"]="taskclosurekit/presets/v2"
        config["execution_policy"]={"schema":"taskclosurekit/execution-policy/v1","file_bytes":32*1024*1024}
        self.config.write_text(json.dumps(config));self.prepare();self.ready()
        self.assertEqual((self.repo/"generated/large").stat().st_size,9*1024*1024)
        self.assertEqual(self.action("evaluate")["state"],"CLAIMABLE")

    def test_final_host_verification_callback_cannot_close_changed_inputs(self):
        from taskclosurekit.integration.adapters import HostReviewProvider,ProviderManifest,VerifiedReview
        case=self
        class Host(HostReviewProvider):
            manifest=ProviderManifest("taskclosurekit/adapters/v1","a"*64,("review.context",),("trusted-host-code",))
            calls=0
            def verify(self,selector,request):
                self.calls+=1
                if self.calls==3:(case.repo/"input.txt").write_text("invalid\n")
                return VerifiedReview(request,"reviewer","session-r",("creator",),("session-c",),True,True,"b"*64,"approve")
        self.value["review"]["independence"]="required";self.input.write_text(json.dumps(self.value))
        self.prepare();self.action("check",preset_id="test");self.action("check",preset_id="build")
        host=Host();self.action("review",review_provider=host,provider_selector="review-run")
        self.assertEqual(self.action("close",review_provider=host)["decision"],"NOT_CLAIMABLE")
        last=json.loads(sorted(self.store.glob("[0-9]*.json"))[-1].read_text())
        self.assertNotEqual(last["kind"],"CLAIM_CLOSED")

    def test_capture_budget_rejects_unknown_coverage_and_bounds_actual_inputs(self):
        self.policy.update(schema="taskclosurekit/integration/v2",coverage="unknown",
            capture_budget={"file_bytes":16*1024*1024,"aggregate_bytes":256*1024*1024,"entries":20000,"seconds":30})
        self.integration.write_text(json.dumps(self.policy))
        with self.assertRaisesRegex(RuntimeError,"unsupported_integration_policy"):self.prepare()
        self.policy["coverage"]="bounded-host-proof-or-rehash";self.integration.write_text(json.dumps(self.policy))
        self.prepare();self.ready();(self.repo/"generated/over-limit").write_bytes(b'x'*(17*1024*1024))
        with self.assertRaisesRegex(RuntimeError,"file_limit"):self.action("close")

    def test_output_above_trusted_runner_and_capture_limit_is_failed_not_partial_pass(self):
        self.policy.update(schema="taskclosurekit/integration/v2",coverage="bounded-host-proof-or-rehash",
            capture_budget={"file_bytes":16*1024*1024,"aggregate_bytes":256*1024*1024,"entries":20000,"seconds":30})
        self.integration.write_text(json.dumps(self.policy));script=self.repo/"check.py"
        script.write_text(script.read_text()+"\nif len(sys.argv)>1:\n Path('generated/too-large').write_bytes(b'x'*(17*1024*1024))\n")
        config=json.loads(self.config.read_text());config["schema"]="taskclosurekit/presets/v2"
        config["execution_policy"]={"schema":"taskclosurekit/execution-policy/v1","file_bytes":16*1024*1024}
        self.config.write_text(json.dumps(config));self.prepare()
        result=self.action("check",preset_id="build")
        self.assertEqual(result["evidence"][-1]["result"],"FAIL")
        self.assertEqual(self.action("close")["decision"],"NOT_CLAIMABLE")

    def test_editable_build_input_stales_checks_and_review_then_reruns(self):
        self.prepare();self.ready()
        (self.repo/"build.json").write_text('{"optimization":2}\n')
        stale=self.action("evaluate")
        self.assertIn("stale_evidence",stale["reasons"])
        self.assertIn("stale_review",stale["reasons"])
        self.ready()
        self.assertEqual(self.action("evaluate")["state"],"CLAIMABLE")
        (self.repo/"check.py").write_text("pass\n")
        self.assertIn("preset_authority_changed",self.action("close")["reasons"])

    def test_hidden_ignored_out_of_scope_writer_cannot_be_excluded(self):
        (self.repo/".gitignore").write_text(".hidden\n")
        self.git("add",".gitignore");self.git("commit","-m","ignore rule")
        self.prepare();self.ready()
        (self.repo/".hidden").write_text("external write\n")
        self.assertIn("write_scope_violation",self.action("close")["reasons"])

    def test_content_equivalence_does_not_hide_unrelated_ref_writes(self):
        self.prepare();self.ready()
        self.git("branch","unrelated")
        blocked=self.action("close")
        self.assertEqual(blocked["decision"],"NOT_CLAIMABLE")
        self.assertIn("authority_changed",blocked["reasons"])

    def test_in_scope_attributes_are_controls_in_worktree_and_index(self):
        self.value["authority"]["write"].extend([".gitattributes",".Gitattributes"])
        self.input.write_text(json.dumps(self.value))
        self.prepare();self.ready()
        for name in (".gitattributes",".Gitattributes"):
            file=self.repo/name;file.write_text("*.txt -text\n")
            self.assertIn("authority_changed",self.action("close")["reasons"])
            self.git("add",name)
            self.assertIn("authority_changed",self.action("close")["reasons"])
            # Source Git mutations are never performed by this test: only the
            # disposable fixture index is restored between independent variants.
            self.git("reset","HEAD","--",name)
            file.unlink()

    def test_check_cannot_create_attributes_as_generated_output_or_corrupt_replay(self):
        script=self.repo/"check.py"
        script.write_text(script.read_text()+"\nif len(sys.argv)>1:\n Path('generated/.Gitattributes').write_text('*.txt -text\\n')\n")
        self.prepare()
        result=self.action("check",preset_id="build")
        self.assertEqual(result["evidence"][-1]["result"],"FAIL")
        self.assertIn("inputs_changed_during_check",result["reasons"])
        self.assertIn("authority_changed",self.action("resume")["reasons"])

    def test_frozen_root_policy_cannot_reclassify_new_inputs(self):
        self.prepare();self.ready()
        self.policy["roots"][0]["rationale"]="Changed trusted rule"
        self.integration.write_text(json.dumps(self.policy))
        blocked=self.action("close")
        self.assertIn("integration_authority_changed",blocked["reasons"])
        self.assertTrue(all(item["state"]=="STALE_AUTHORITY" for item in blocked["freshness"]))

    def test_dependency_lockfile_does_not_establish_installed_immutability(self):
        self.policy["roots"]=[{"path":"dependencies","type":"dependency","acceptance":"include","rationale":"Installed bytes", "dependency_provenance":{"method":"immutable-host","identity_inputs":["build.json"]}}]
        self.integration.write_text(json.dumps(self.policy))
        with self.assertRaisesRegex(RuntimeError,"immutable_dependency_proof_unavailable"):
            self.prepare()
        self.assertFalse(self.store.exists())

    def test_exclusion_cannot_overlap_source_or_authority_inputs(self):
        self.policy["roots"][0]["path"]="input.txt"
        self.integration.write_text(json.dumps(self.policy))
        with self.assertRaisesRegex(RuntimeError,"unsafe_acceptance_exclusion"):
            self.prepare()

    def test_status_resume_evaluate_are_idempotent_and_have_no_storage_writes(self):
        self.prepare();self.ready()
        before={p.name:p.read_bytes() for p in self.store.iterdir()}
        results=[self.action(op) for op in ("status","resume","evaluate","status")]
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.store.iterdir()})
        self.assertTrue(all(r["decision"]=="CLAIMABLE" and r["capabilities"]["schema"]=="taskclosurekit/adapters/v1" for r in results))

    def test_review_strings_without_host_identity_context_proof_do_not_close(self):
        from taskclosurekit.integration.adapters import HostReviewProvider, ProviderManifest, VerifiedReview
        from dataclasses import replace
        class Host(HostReviewProvider):
            manifest=ProviderManifest("taskclosurekit/adapters/v1","a"*64,("review.context",),("trusted-host-code",))
            def verify(self,selector,request):
                return replace(VerifiedReview(request,"reviewer","session-r",("creator",),("session-c",),True,True,"b"*64,"approve"),**self.change)
        self.value["review"]["independence"]="required";self.input.write_text(json.dumps(self.value))
        self.prepare();self.action("check",preset_id="test");self.action("check",preset_id="build")
        for change in ({"identity_verified":False},{"fresh_context_verified":False},{"reviewer_agent":"creator"},{"reviewer_session":"session-c"}):
            provider=Host();provider.change=change
            with self.assertRaisesRegex(RuntimeError,"host_review_not_independent"):
                self.action("review",review_provider=provider,provider_selector="opaque-run")
        self.assertIn("independence_unknown",self.action("close")["reasons"])

    def test_expired_revoked_self_issued_and_replayed_capabilities_fail_closed(self):
        from taskclosurekit.integration.adapters import AuthorityProvider, ProviderManifest, VerifiedCapability
        from dataclasses import replace
        class Issuer(AuthorityProvider):
            manifest=ProviderManifest("taskclosurekit/adapters/v1","c"*64,("authority.one-time",),("trusted-host-code",))
            def verify(self,selector,request,at_ns):
                return replace(VerifiedCapability(request,(request.operation,),at_ns-2,at_ns+10**9,False,True,True,"once","d"*64),**self.change)
            def consume(self,selector,capability):
                raise RuntimeError("capability_replayed")
        self.prepare();self.ready();before={p.name:p.read_bytes() for p in self.store.iterdir()}
        for change in ({"expires_ns":1},{"revoked":True},{"externally_issued":False},{"identity_verified":False},{}):
            issuer=Issuer();issuer.change=change
            with self.assertRaises(RuntimeError):
                application.execute(str(self.store),"close",authority_provider=issuer,capability_selector="external-grant")
        after={p.name:p.read_bytes() for p in self.store.iterdir()}
        self.assertTrue(all(after[name]==content for name,content in before.items()))
        self.assertEqual(len(after),len(before)+1)
        self.assertIn("capability_attempt_requires_recovery",self.action("resume")["reasons"])

    def test_out_of_scope_mode_delete_and_link_changes_block(self):
        self.prepare();self.ready()
        authority=self.repo/"README.md";content=authority.read_bytes()
        authority.chmod(0o755)
        self.assertEqual(self.action("close")["decision"],"NOT_CLAIMABLE")
        authority.chmod(0o644);authority.unlink()
        with self.assertRaises(RuntimeError):self.action("close")
        authority.write_bytes(content)
        (self.repo/"escape").symlink_to(self.root/"outside")
        with self.assertRaisesRegex(RuntimeError,"symlink_unsupported"):self.action("close")
