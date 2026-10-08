"""Real C behavior, semantic type checks and compiler output on disposable repositories."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from taskclosurekit import application
from test_v2_cycle import ConfirmedOperator, ROOT

CLANG = Path("/Library/Developer/CommandLineTools/usr/bin/clang")
if not CLANG.exists():
    found = shutil.which("clang")
    CLANG = Path(found).resolve() if found else None

@unittest.skipUnless(CLANG and CLANG.exists(), "Existing C compiler required; no installation")
class EngineeringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.store = self.root / "store"
        self.input = self.root / "contract.json"
        self.config = self.root / "presets.json"
        self.env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "Fixture",
                    "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "Fixture",
                    "GIT_COMMITTER_EMAIL": "fixture@example.invalid", "PYTHONDONTWRITEBYTECODE": "1"}
        self.git("init", "--template=", "--initial-branch=master")
        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "README.md").write_text("Immutable task authority.\n")
        (self.repo / "src/foo.c").write_text("int increment(int value) { return value; }\n")
        (self.repo / "tests/regression.c").write_text("int increment(int); int main(void) { return increment(2) == 3 ? 0 : 1; }\n")
        for name in ("manifest.json", "dependencies.lock", "compiler.json"):
            (self.repo / name).write_text("{}\n")
        launcher = """import os, subprocess, sys
from pathlib import Path
compiler = COMPILER
sdk = SDK
mode = sys.argv[1]
if mode == 'typecheck':
    args = [compiler, '-Werror', '-fsyntax-only', 'src/foo.c']
else:
    Path('.build').mkdir(exist_ok=True)
    args = [compiler, 'src/foo.c', 'tests/regression.c', '-o', '.build/regression'] if mode == 'tests' else [compiler, '-c', 'src/foo.c', '-o', '.build/foo.o']
if sdk:
    args[1:1] = ['-isysroot', sdk]
result = subprocess.run(args).returncode
if result == 0 and mode == 'tests':
    result = subprocess.run([str(Path('.build/regression').absolute())]).returncode
sys.exit(result)
""".replace("COMPILER", repr(str(CLANG))).replace("SDK", repr(str(Path("/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk").resolve()) if sys.platform == "darwin" else ""))
        (self.repo / "checks.py").write_text(launcher)
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.presets = []
        for mode in ("tests", "typecheck", "build"):
            self.presets.append({"id": "project-" + mode, "executable": str(Path(sys.executable).resolve()),
                "argv": ["-B", "checks.py", mode], "cwd_rule": "contract-repository",
                "environment_allowlist": {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": ".build"},
                "timeout": 5, "output_limit": 65536,
                "permitted_writes": [".build"],
                "relevant_inputs": {"source": ["src"], "tests": ["tests"], "manifests": ["manifest.json"],
                                    "lockfiles": ["dependencies.lock"], "config": ["compiler.json"]},
                "authority_inputs": ["checks.py"], "runtime_inputs": [str(CLANG)]})
        self.config_value = {"schema": "taskclosurekit/presets/v1", "presets": self.presets}
        self.config.write_text(json.dumps(self.config_value))
        self.value = {"schema": "taskclosurekit/v2", "task": {"id": "engineering", "title": "Fix increment regression"},
            "repository": str(self.repo), "authority": {"task": {"issuer": "human"},
            "read": ["README.md", "src", "tests", "checks.py", "manifest.json", "dependencies.lock", "compiler.json"],
            "write": ["src/foo.c", ".build"], "execution": {"presets": [p["id"] for p in self.presets]}},
            "sources": ["README.md"], "acceptance": [
                {"id": "regression", "required": True, "evidence": {"all_of": ["project-tests"]}},
                {"id": "types", "required": True, "evidence": {"all_of": ["project-typecheck"]}},
                {"id": "build", "required": False, "evidence": {"all_of": ["project-tests", "project-build"]}}],
            "review": {"required": True, "independence": "not_required"},
            "claim": {"type": "configured-acceptance-satisfied"}, "closure": {"authority": "human"}}
        self.input.write_text(json.dumps(self.value))

    def git(self, *args):
        return subprocess.run([shutil.which("git"), *args], cwd=self.repo, env=self.env, check=True, capture_output=True).stdout

    def action(self, operation, **kw):
        return application.execute(str(self.store), operation, operator=ConfirmedOperator(), **kw)

    def prepare(self):
        application.create(str(self.store), str(self.input), preset_config=str(self.config))
        self.assertEqual(self.action("authorize")["state"], "AUTHORIZED")
        self.assertEqual(self.action("baseline")["state"], "BASELINED")

    def fix(self):
        (self.repo / "src/foo.c").write_text("int increment(int value) { return value + 1; }\n")

    def check(self, mode):
        return self.action("check", preset_id="project-" + mode)

    def test_slice_a_real_bug_fix_tests_typecheck_review_claim_and_close(self):
        self.prepare()
        self.assertEqual(self.check("tests")["evidence"][-1]["result"], "FAIL")
        self.fix()
        self.assertEqual(self.check("tests")["evidence"][-1]["result"], "PASS")
        self.assertIn("missing_required_evidence", self.action("evaluate")["reasons"])
        self.assertEqual(self.check("typecheck")["evidence"][-1]["result"], "PASS")
        self.action("review", human=True)
        result = self.action("evaluate")
        self.assertEqual(result["state"], "CLAIMABLE")
        self.assertNotIn("closure", result)
        self.assertEqual(self.action("close")["state"], "CLOSED")

    def test_config_cannot_self_authorize_trivially_passing_replacement(self):
        self.prepare(); self.fix(); self.check("tests"); self.check("typecheck")
        self.config_value["presets"][0]["argv"] = ["-c", "pass"]
        self.config.write_text(json.dumps(self.config_value))
        before = sorted(self.store.glob("[0-9]*.json"))
        result = self.check("tests")
        self.assertEqual(result["decision"], "NOT_CLAIMABLE")
        self.assertIn("preset_authority_changed", result["reasons"])
        self.assertTrue(all(item["state"] == "STALE_AUTHORITY" for item in result["freshness"]))
        self.assertEqual(before, sorted(self.store.glob("[0-9]*.json")))
        self.assertEqual(result["next_action"], "new_task")

    def test_real_type_error_fails_and_latest_fail_hides_old_pass(self):
        self.prepare(); self.fix(); self.check("tests"); self.check("typecheck")
        (self.repo / "src/foo.c").write_text('int increment(int value) { return "wrong type"; }\n')
        result = self.check("typecheck")
        self.assertEqual(result["evidence"][-1]["result"], "FAIL")
        self.assertIn("required_evidence_failed", self.action("evaluate")["reasons"])
        self.fix()
        self.assertIn("required_evidence_failed", self.action("evaluate")["reasons"])

    def test_slice_b_tests_and_build_compose_and_compiler_config_stales_review(self):
        self.value["authority"]["write"].append("compiler.json")
        self.value["acceptance"] = [{"id": "feature", "required": True,
                                     "evidence": {"all_of": ["project-tests", "project-build"]}}]
        self.input.write_text(json.dumps(self.value))
        self.prepare(); self.fix(); self.check("tests")
        self.assertIn("missing_required_evidence", self.action("evaluate")["reasons"])
        self.assertEqual(self.check("build")["evidence"][-1]["result"], "PASS")
        self.assertGreater((self.repo / ".build/foo.o").stat().st_size, 0)
        # Full conservative input binding includes newly generated outputs.
        self.check("tests"); self.action("review", human=True)
        self.assertEqual(self.action("evaluate")["state"], "CLAIMABLE")
        (self.repo / "compiler.json").write_text('{"strict": true}\n')
        stale = self.action("evaluate")
        self.assertIn("stale_evidence", stale["reasons"])
        self.assertIn("stale_review", stale["reasons"])
        self.assertTrue(all(item["state"] == "STALE_INPUT" for item in stale["freshness"]))
        self.assertEqual(self.action("review", human=True)["decision"], "NOT_CLAIMABLE")

    def test_slice_c_scope_violation_overrides_all_passing_checks(self):
        self.prepare(); self.fix(); self.check("build"); self.check("tests"); self.check("typecheck")
        self.action("review", human=True)
        self.assertEqual(self.action("evaluate")["state"], "CLAIMABLE")
        (self.repo / "other.md").write_text("Unauthorized write.\n")
        result = self.action("close")
        self.assertIn("write_scope_violation", result["reasons"])
        self.assertEqual(result["decision"], "NOT_CLAIMABLE")

    def test_real_flow_required_independence_is_still_unknown(self):
        self.value["review"]["independence"] = "required"
        self.input.write_text(json.dumps(self.value))
        self.prepare(); self.fix(); self.check("tests"); self.check("typecheck")
        self.action("review", human=True)
        result = self.action("evaluate")
        self.assertEqual(result["independence"], "UNKNOWN")
        self.assertIn("independence_unknown", result["reasons"])
        self.assertEqual(result["review"]["verdict"], "approve")
        self.assertEqual(result["review"]["trust"], "operator_confirmed")
        self.assertEqual(result["review"]["freshness"], "CURRENT")
        self.assertEqual(result["authority"]["identity"], "unverified")

    def test_dispatcher_change_is_authority_and_cannot_pass_its_own_new_command(self):
        self.prepare(); self.fix(); self.check("tests")
        (self.repo / "checks.py").write_text("pass\n")
        result = self.check("tests")
        self.assertIn("preset_authority_changed", result["reasons"])
        self.assertEqual(result["freshness"][0]["state"], "STALE_AUTHORITY")

    def test_cli_config_and_cross_process_structured_handoff_and_exit_codes(self):
        command = [sys.executable, "-m", "taskclosurekit", "--store", str(self.store)]
        def cli(*args):
            return subprocess.run([*command, *args, "--json"], cwd=ROOT, env=self.env, capture_output=True, text=True)
        created = cli("task", "create", str(self.input), "--preset-config", str(self.config))
        self.assertEqual(created.returncode, 0, created.stdout)
        self.action("authorize"); self.action("baseline")
        self.fix(); self.check("tests"); self.check("typecheck")
        before = {p.name: p.read_bytes() for p in self.store.iterdir()}
        status = cli("status", "--next")
        result = json.loads(status.stdout)
        self.assertEqual(status.returncode, 1)
        self.assertEqual(result["handoff"]["next_permitted_action"], "review")
        self.assertEqual(len(result["handoff"]["current_evidence"]), 2)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.store.iterdir()})
        self.action("review", human=True)
        self.assertEqual(cli("evaluate").returncode, 0)
        self.assertEqual(cli("status").returncode, 0)
        (self.repo / "src/foo.c").write_text("int increment(int value) { return value + 2; }\n")
        stale = cli("resume")
        self.assertEqual(stale.returncode, 4)
        self.assertTrue(json.loads(stale.stdout)["handoff"]["stale_evidence"])

    def test_direct_project_launcher_cannot_be_left_unbound(self):
        self.presets[0]["authority_inputs"] = []
        self.config.write_text(json.dumps(self.config_value))
        with self.assertRaisesRegex(RuntimeError, "undeclared_command_authority"):
            application.create(str(self.store), str(self.input), preset_config=str(self.config))

    def test_unpermitted_runner_write_is_failed_and_never_expands_scope(self):
        launcher = self.repo / "checks.py"
        launcher.write_text(launcher.read_text() + "\n")
        content = launcher.read_text().replace("sys.exit(result)", "Path('other.md').write_text('unpermitted')\nsys.exit(result)")
        launcher.write_text(content)
        self.prepare(); self.fix()
        result = self.check("tests")
        self.assertEqual(result["evidence"][-1]["result"], "FAIL")
        self.assertIn("inputs_changed_during_check", result["reasons"])
        self.assertIn("write_scope_violation", self.action("evaluate")["reasons"])

    def test_runtime_resource_drift_is_environment_and_requires_new_task(self):
        resource = self.root / "runtime-resource"
        resource.write_text("runtime-v1")
        self.presets[0]["runtime_inputs"].append(str(resource))
        self.config.write_text(json.dumps(self.config_value))
        self.prepare(); self.fix(); self.check("tests"); self.check("typecheck")
        resource.write_text("runtime-v2")
        result = self.action("evaluate")
        self.assertIn("preset_environment_changed", result["reasons"])
        self.assertTrue(all(x["state"] == "STALE_ENVIRONMENT" for x in result["freshness"]))
        self.assertEqual(result["next_action"], "new_task")

    def test_config_change_while_authorizing_cannot_refresh_approval_binding(self):
        application.create(str(self.store), str(self.input), preset_config=str(self.config))
        config = self.config
        class RacingOperator(ConfirmedOperator):
            def confirm(self, operation, binding):
                config.write_text(config.read_text() + " ")
                return super().confirm(operation, binding)
        with self.assertRaisesRegex(RuntimeError, "inputs_changed_during_confirmation"):
            application.execute(str(self.store), "authorize", operator=RacingOperator())
        self.assertEqual(len(list(self.store.glob("[0-9]*.json"))), 1)

    def test_declared_writes_and_inputs_cannot_expand_contract_authority(self):
        for field, value, reason in [("permitted_writes", [".build", "outside"], "preset_write_outside_authority"),
                                     ("authority_inputs", ["src/foo.c"], "preset_authority_mutable")]:
            with self.subTest(field=field):
                original = self.presets[0][field]
                self.presets[0][field] = value
                self.config.write_text(json.dumps(self.config_value))
                with self.assertRaisesRegex(RuntimeError, reason):
                    application.create(str(self.store), str(self.input), preset_config=str(self.config))
                self.presets[0][field] = original

    def test_signed_malformed_registry_fails_semantic_replay(self):
        self.prepare()
        import hashlib, hmac
        from taskproof.snapshot import canonical
        key = (self.store / "key").read_bytes()
        previous = "0" * 64
        for file_path in sorted(self.store.glob("[0-9]*.json")):
            event = json.loads(file_path.read_text())
            if event["kind"] == "CONTRACT_CREATED":
                event["payload"]["registry"]["presets"][0]["timeout"] = True
            event["previous"] = previous
            body = {key: value for key, value in event.items() if key != "signature"}
            event["signature"] = hmac.new(key, canonical(body), hashlib.sha256).hexdigest()
            file_path.write_bytes(canonical(event)); file_path.chmod(0o600)
            previous = hashlib.sha256(canonical(event)).hexdigest()
        with self.assertRaisesRegex(RuntimeError, "invalid_runner_limits"):
            self.action("status")

    def test_output_limit_keeps_bounded_failed_receipt(self):
        (self.repo / "checks.py").write_text("import sys\nprint('x' * 4096)\n")
        self.presets[0]["output_limit"] = 128
        self.config.write_text(json.dumps(self.config_value))
        self.prepare()
        result = self.check("tests")
        self.assertIn("check_output_limit", result["reasons"])
        self.assertEqual(result["evidence"][-1]["result"], "FAIL")

    def test_contract_cannot_supply_executable_or_shell_with_project_ids(self):
        self.value["authority"]["execution"]["shell"] = "pass"
        self.input.write_text(json.dumps(self.value))
        with self.assertRaisesRegex(RuntimeError, "unknown_or_missing_fields"):
            application.create(str(self.store), str(self.input), preset_config=str(self.config))

    def test_timeout_keeps_bounded_failed_receipt(self):
        (self.repo / "checks.py").write_text("import time\ntime.sleep(2)\n")
        self.presets[0]["timeout"] = 0.05
        self.config.write_text(json.dumps(self.config_value))
        self.prepare()
        result = self.check("tests")
        self.assertIn("check_timeout", result["reasons"])
        self.assertEqual(result["evidence"][-1]["result"], "FAIL")

    def test_source_tests_manifest_lock_and_config_changes_are_stale_input(self):
        mutable = ["tests/regression.c", "manifest.json", "dependencies.lock", "compiler.json"]
        self.value["authority"]["write"].extend(mutable)
        self.input.write_text(json.dumps(self.value))
        self.prepare(); self.fix(); self.check("tests"); self.check("typecheck")
        for filename in ["src/foo.c", *mutable]:
            with self.subTest(filename=filename):
                file_path = self.repo / filename
                original = file_path.read_text()
                file_path.write_text(original + "\n")
                result = self.action("evaluate")
                self.assertIn("stale_evidence", result["reasons"])
                self.assertTrue(all(item["state"] == "STALE_INPUT" for item in result["freshness"]))
                file_path.write_text(original)

    def test_package_script_manifest_cannot_be_undeclared_command_authority(self):
        from taskclosurekit.execution.presets import Preset, validate_dispatch
        package = Preset("project-bun", "/absolute/bun", ("run", "test"), "contract-repository", (), 5, 65536, ())
        with self.assertRaisesRegex(RuntimeError, "undeclared_command_authority"):
            validate_dispatch(package, {"package.json": {"kind": "file"}})

    def test_launcher_arguments_cannot_disguise_direct_dispatch_as_interpreter_mode(self):
        self.presets[0]["authority_inputs"] = []
        self.presets[0]["argv"] = ["-B", "checks.py", "-m"]
        self.config_value["presets"] = [self.presets[0]]
        self.value["authority"]["execution"]["presets"] = ["project-tests"]
        self.value["acceptance"] = [self.value["acceptance"][0]]
        self.value["authority"]["write"].append("checks.py")
        self.input.write_text(json.dumps(self.value))
        self.config.write_text(json.dumps(self.config_value))
        with self.assertRaisesRegex(RuntimeError, "undeclared_command_authority"):
            application.create(str(self.store), str(self.input), preset_config=str(self.config))

    def test_interpreter_named_script_module_and_absolute_launcher_are_bound(self):
        for index, (argv, filename) in enumerate([(["-B", "pytest"], "pytest"),
                               (["-B", "-m", "projectcheck"], "projectcheck.py"),
                               (["-B", str(self.repo / "projectcheck.py")], "projectcheck.py")]):
            with self.subTest(argv=argv):
                self.store = self.root / ("store-" + str(index))
                (self.repo / filename).write_text("pass\n")
                self.presets[0]["authority_inputs"] = []
                self.presets[0]["argv"] = argv
                self.config_value["presets"] = [self.presets[0]]
                self.value["authority"]["execution"]["presets"] = ["project-tests"]
                self.value["acceptance"] = [self.value["acceptance"][0]]
                if filename not in self.value["authority"]["read"]:
                    self.value["authority"]["read"].append(filename)
                    self.value["authority"]["write"].append(filename)
                self.input.write_text(json.dumps(self.value)); self.config.write_text(json.dumps(self.config_value))
                with self.assertRaisesRegex(RuntimeError, "undeclared_command_authority"):
                    application.create(str(self.store), str(self.input), preset_config=str(self.config))
