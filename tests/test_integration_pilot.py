"""Two synthetic toolchains; no application checkout or installation is touched."""
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from taskclosurekit import application
from taskclosurekit._primitives import snapshot
import test_integration_contract as python_fixture
import test_v21_engineering as c_fixture


class IntegrationPilotTests(unittest.TestCase):
    def scenario(self, fixture, toolchain, generated, source, changed_source, preset_ids):
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        hooks=fixture.repo/".git/hooks";hooks.mkdir(exist_ok=True)
        marker=fixture.root/"hook-executed"
        (hooks/"pre-commit").write_text("#!/bin/sh\nexit 0\n")
        (hooks/"pre-commit").chmod(0o755)
        (hooks/"post-index-change").write_text("#!/bin/sh\nprintf unexpected > '"+str(marker)+"'\n")
        (hooks/"post-index-change").chmod(0o755)
        fixture.git("repack","-ad")
        if toolchain=="C":
            linked=fixture.root/"linked"
            fixture.git("worktree","add","--detach",str(linked),"HEAD")
            fixture.repo=linked;fixture.value["repository"]=str(linked)
        fixture.value["authority"]["write"].append(".gitignore")
        if toolchain=="C":
            fixture.value["authority"]["write"].append("compiler.json")
        fixture.value["acceptance"]=[{"id":"configured","required":True,"evidence":{"all_of":preset_ids}}]
        fixture.input.write_text(json.dumps(fixture.value))
        integration=fixture.root/"integration.json"
        integration.write_text(json.dumps({"schema":"taskclosurekit/integration/v1","stage_equivalence":"content",
            "roots":[{"path":generated,"type":"generated","acceptance":"exclude","rationale":"These outputs are not inputs to configured checks.","dependency_provenance":None}],"coverage":"bounded-full-rehash"}))
        io={"completed_file_reads":0,"bytes_read":0};durations={};false_closes=0;negative=0
        original=snapshot.regular
        def counted(*args,**kwargs):
            result=original(*args,**kwargs)
            io["completed_file_reads"]+=1;io["bytes_read"]+=result[0]["size"]
            return result
        def action(op,**kwargs):
            start=time.monotonic();result=fixture.action(op,**kwargs)
            durations[op]=durations.get(op,0)+time.monotonic()-start
            return result
        started=time.monotonic()
        with patch.object(snapshot,"regular",side_effect=counted):
            application.create(str(fixture.store),str(fixture.input),preset_config=str(fixture.config),integration_config=str(integration))
            action("authorize");action("baseline")
            # Missing gates must not close. C additionally exercises a real failure.
            missing=action("close");negative+=1;false_closes+=int(missing["state"]=="CLOSED")
            if changed_source:
                failed=action("check",preset_id=preset_ids[0])
                self.assertEqual(failed["evidence"][-1]["result"],"FAIL")
                negative+=1;false_closes+=int(action("close")["state"]=="CLOSED")
                (fixture.repo/source).write_text(changed_source)
            fixture.git("add",source)
            # Hooks may run during this separately authorized fixture mutation.
            marker.unlink(missing_ok=True)
            for preset_id in preset_ids:
                self.assertEqual(action("check",preset_id=preset_id)["evidence"][-1]["result"],"PASS")
            action("review",human=True)
            self.assertEqual(action("evaluate")["state"],"CLAIMABLE")
            self.assertFalse(marker.exists(),"read-only inspection must not run hooks")
            # Editable ordinary build inputs rerun; changed execution scripts do not.
            build_input="compiler.json" if toolchain=="C" else "build.json"
            if build_input not in fixture.value["authority"]["write"]:
                # Scope is frozen; this pilot does not widen it after CREATE.
                build_input=None
            if build_input:
                file=fixture.repo/build_input;file.write_text('{"strict":true}\n')
                negative+=1;false_closes+=int(action("close")["state"]=="CLOSED")
                fixture.git("add",build_input);marker.unlink(missing_ok=True)
                for preset_id in preset_ids:action("check",preset_id=preset_id)
                action("review",human=True)
            fixture.git("commit","--allow-empty","-m","content-equivalent result")
            marker.unlink(missing_ok=True)
            current=action("evaluate")
            self.assertEqual(current["state"],"CLAIMABLE")
            self.assertEqual(current["result_identity"]["stage"],"committed")
            # Hidden ignored write remains in the complete byte inventory.
            hidden=fixture.repo/".external-hidden";hidden.write_text("unauthorized external writer\n")
            negative+=1;false_closes+=int(action("close")["state"]=="CLOSED")
            hidden.unlink()
            closed=action("close")
            self.assertEqual(closed["state"],"CLOSED")
            self.assertFalse(marker.exists())
        self.assertEqual(false_closes,0)
        measured={"toolchain":toolchain,"wall_seconds":round(time.monotonic()-started,6),"operation_seconds":durations,
            "hash_io":io,"negative_cases":negative,"false_closes":false_closes,
            "io_scope":"Completed bounded regular-file reads in this process; excludes Git/compiler child IO and incomplete reads."}
        destination=os.environ.get("TCK_PILOT_METRICS")
        if destination:
            target=Path(destination).resolve()
            if snapshot.path_within(target,Path(__file__).resolve().parents[1]):
                raise RuntimeError("pilot_evidence_must_be_external")
            target.mkdir(parents=True,exist_ok=True)
            (target/(toolchain.lower()+".json")).write_text(json.dumps(measured,sort_keys=True,indent=2))
        # Timebox is a regression budget, not a claim that one workday elapsed.
        self.assertLess(measured["wall_seconds"],120)
        # Measured compiler identity dominates C hashing; there is no trusted
        # installed-runtime immutability proof, so bytes cannot be reused safely.
        self.assertLess(io["bytes_read"],8*1024*1024*1024 if toolchain=="C" else 512*1024*1024)

    def test_python_toolchain_packs_outputs_build_input_and_commit(self):
        self.scenario(python_fixture.IntegrationContractTests(),"Python","generated","input.txt",None,["test","build"])

    @unittest.skipUnless(c_fixture.CLANG and c_fixture.CLANG.exists(),"Existing C compiler required; no installation")
    def test_c_toolchain_linked_packs_real_failure_and_commit(self):
        self.scenario(c_fixture.EngineeringTests(),"C",".build","src/foo.c","int increment(int value) { return value + 1; }\n",["project-tests","project-build"])
