"""Trusted configurable checks run against disposable roots, without shell or installation."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from taskclosurekit.domain.contract import parse_contract
from taskclosurekit.execution.presets import prepare_config, validate_record
from taskclosurekit.execution.runner import run


class ExecutionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp" if Path("/private/tmp").is_dir() else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "README.md").write_text("Task authority.\n")
        (self.repo / "input.txt").write_text("Check data.\n")
        self.config = self.root / "presets.json"
        self.preset = {"id": "real-check", "executable": str(Path(sys.executable).resolve()),
            "argv": ["-I", "-B", "-c", "import time; time.sleep(5.1); print('completed')"],
            "cwd_rule": "contract-repository", "environment_allowlist": {"PATH": "/usr/bin:/bin"},
            "timeout": 7, "output_limit": 65536, "permitted_writes": [],
            "relevant_inputs": {"source": ["input.txt"], "tests": [], "manifests": [], "lockfiles": [], "config": []},
            "authority_inputs": [], "runtime_inputs": []}
        self.value = {"schema": "taskclosurekit/presets/v2", "execution_policy": {
            "schema": "taskclosurekit/execution-policy/v1"}, "presets": [self.preset]}
        self.contract = parse_contract({"schema": "taskclosurekit/v2",
            "task": {"id": "bounded-check", "title": "Run a real configured check"},
            "repository": str(self.repo), "authority": {"task": {"issuer": "human"},
                "read": ["README.md", "input.txt"], "write": [], "execution": {"presets": ["real-check"]}},
            "sources": ["README.md"], "acceptance": [{"id": "check", "required": True,
                "evidence": {"all_of": ["real-check"]}}],
            "review": {"required": False, "independence": "not_required"},
            "claim": {"type": "configured-acceptance-satisfied"}, "closure": {"authority": "human"}})

    def prepare(self):
        from types import SimpleNamespace
        self.config.write_text(json.dumps(self.value))
        record = prepare_config(str(self.config), self.contract, SimpleNamespace(path=self.root / "store"))
        return record, validate_record(record, self.contract)

    def test_real_configured_check_can_run_longer_than_five_seconds(self):
        record, registry = self.prepare()
        result = run("real-check", str(self.repo), registry)
        self.assertEqual(record["schema"], "taskclosurekit/presets/v2")
        self.assertEqual(result["kind"], "bounded-output-v2")
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["timeout"])
        self.assertGreaterEqual(result["duration_ms"], 5000)
        self.assertIn("completed", result["diagnostics"]["stdout"])

    def test_explicit_external_toolchain_is_bound_and_has_no_ambient_secrets(self):
        from unittest.mock import patch
        tools = self.root / "toolchain"
        tools.mkdir()
        executable = tools / "fixture-tool"
        executable.write_text("#!" + str(Path(sys.executable).resolve()) + "\nimport os\nprint(os.environ.get('PRIVATE_TOKEN', 'absent'))\n")
        executable.chmod(0o755)
        self.preset.update(argv=["-I", "-B", "-c", "import subprocess; subprocess.run(['fixture-tool'],check=True)"],
            timeout=2, runtime_inputs=[str(executable)],
            environment_allowlist={"PATH": str(tools) + ":/usr/bin:/bin"})
        with patch.dict(os.environ, {"PRIVATE_TOKEN": "not-for-child"}):
            record, registry = self.prepare()
            result = run("real-check", str(self.repo), registry)
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("absent", result["diagnostics"]["stdout"])
        self.assertEqual(record["path_bindings"]["resolutions"]["real-check"]["fixture-tool"], str(executable))

    def test_result_diagnostics_redact_secrets_and_required_output_loss_blocks(self):
        from taskclosurekit.execution.runner import result_reason, validate_result
        self.preset.update(argv=["-I", "-B", "-c", "print('token=private-fixture-token'); print('x' * 80)"], timeout=2)
        self.value["execution_policy"].update(diagnostic_limit=64, require_complete_diagnostics=True)
        _, registry = self.prepare()
        result = run("real-check", str(self.repo), registry)
        validate_result(result, registry.get("real-check"))
        self.assertNotIn("private-fixture-token", result["diagnostics"]["stdout"])
        self.assertIn("[REDACTED]", result["diagnostics"]["stdout"])
        self.assertTrue(result["output_loss"])
        self.assertEqual(result_reason(result, registry.get("real-check")), "check_evidence_lost")

    def test_builtin_runner_cleans_successful_leaders_background_group(self):
        from taskclosurekit._primitives.runner import run_check
        marker = self.root / "late-child"
        ready = self.root / "child-ready"
        child = "import time;from pathlib import Path;Path(" + repr(str(ready)) + ").touch();time.sleep(0.3);Path(" + repr(str(marker)) + ").touch()"
        executable = self.root / "fake-git"
        executable.write_text("#!" + str(Path(sys.executable).resolve()) + "\nimport subprocess,sys,time\nfrom pathlib import Path\nsubprocess.Popen([sys.executable,'-c'," + repr(child) + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nwhile not Path(" + repr(str(ready)) + ").exists():\n time.sleep(0.01)\n")
        executable.chmod(0o755)
        result = run_check(executable, str(self.repo))
        self.assertEqual(result["exit_code"], 0)
        time.sleep(0.4)
        self.assertFalse(marker.exists(), "owned background child survived success cleanup")

    def test_explicit_manifest_data_changes_preserve_dispatcher_authority_but_commands_do_not(self):
        from taskclosurekit.execution.presets import observe
        from taskclosurekit._primitives.snapshot import inventory
        # A disposable bound executable stands in for the already installed manager;
        # this test observes dispatch binding and never executes a package command.
        executable = self.root / "npm"
        executable.write_text("manager fixture\n")
        executable.chmod(0o755)
        manifest = self.repo / "package.json"
        manifest.write_text(json.dumps({"scripts": {"check": "fixed-check"}, "description": "before"}))
        self.preset.update(executable=str(executable), argv=["run", "check"], timeout=2,
            dispatcher_inputs=[{"path": "package.json", "kind": "package-scripts/v1", "mutable_fields": ["description"]}],
            relevant_inputs={"source": ["input.txt"], "tests": [], "manifests": ["package.json"], "lockfiles": [], "config": []})
        from dataclasses import replace
        self.contract = replace(self.contract, authority=replace(self.contract.authority,
            read=("README.md", "input.txt", "package.json"), write=("package.json",)))
        record, _ = self.prepare()
        original = record["dispatcher_bindings"]
        manifest.write_text(json.dumps({"scripts": {"check": "fixed-check"}, "description": "after"}))
        self.assertEqual(observe(record, self.contract, inventory(self.repo))["dispatcher_bindings"], original)
        manifest.write_text(json.dumps({"scripts": {"check": "changed-command"}, "description": "after"}))
        self.assertNotEqual(observe(record, self.contract, inventory(self.repo))["dispatcher_bindings"], original)
        manifest.write_text(json.dumps({"scripts": {"check": "fixed-check"}, "description": "after", "custom_loader": "unhandled"}))
        self.assertNotEqual(observe(record, self.contract, inventory(self.repo))["dispatcher_bindings"], original)

    def test_external_script_cannot_hide_unbound_shebang_runtime(self):
        tools = self.root / "tools"
        tools.mkdir()
        executable = tools / "fixture-tool"
        executable.write_text("#!/usr/bin/env python3\nprint('hidden interpreter')\n")
        executable.chmod(0o755)
        self.preset.update(argv=["-I", "-B", "-c", "pass"], timeout=2, runtime_inputs=[str(executable)],
            environment_allowlist={"PATH": str(tools) + ":/usr/bin:/bin"})
        with self.assertRaisesRegex(RuntimeError, "unbound_shebang_runtime"):
            self.prepare()

    def test_unbounded_or_invalid_policy_fails_before_execution_and_v1_remains_strict(self):
        self.preset.update(argv=["-I", "-B", "-c", "raise SystemExit('must not run')"], timeout=2)
        for policy in ({"timeout_limit": True}, {"output_limit": 0}, {"cpu_seconds": 86401},
                       {"file_bytes": -1}, {"open_files": 2}, {"address_space_bytes": 0},
                       {"diagnostic_limit": 65537}, {"unknown_resource": 1}):
            with self.subTest(policy=policy):
                self.value["execution_policy"] = {"schema": "taskclosurekit/execution-policy/v1", **policy}
                with self.assertRaisesRegex(RuntimeError, "invalid_execution_policy"):
                    self.prepare()
        self.value = {"schema": "taskclosurekit/presets/v1", "presets": [self.preset]}
        self.preset["timeout"] = 7
        with self.assertRaisesRegex(RuntimeError, "invalid_runner_limits"):
            self.prepare()

    def test_declared_toolchain_resolution_drift_is_not_silently_rebound(self):
        from taskclosurekit.execution.presets import observe
        from taskclosurekit._primitives.snapshot import inventory
        early = self.root / "early"
        tools = self.root / "tools"
        early.mkdir()
        tools.mkdir()
        executable = tools / "fixture-tool"
        executable.write_text("#!" + str(Path(sys.executable).resolve()) + "\nprint('approved')\n")
        executable.chmod(0o755)
        self.preset.update(argv=["-I", "-B", "-c", "pass"], timeout=2, runtime_inputs=[str(executable)],
            environment_allowlist={"PATH": str(early) + ":" + str(tools) + ":/usr/bin:/bin"})
        record, _ = self.prepare()
        shadow = early / "fixture-tool"
        shadow.write_text("unapproved replacement\n")
        shadow.chmod(0o755)
        self.assertEqual(observe(record, self.contract, inventory(self.repo))["path_bindings"], {"unknown": True})
        self.assertEqual(record["path_bindings"]["resolutions"]["real-check"]["fixture-tool"], str(executable))

    def test_json_credentials_are_redacted_in_bounded_diagnostics(self):
        self.preset.update(argv=["-I", "-B", "-c", "print('{\"api_key\": \"private-fixture-value\"}')"], timeout=2)
        _, registry = self.prepare()
        result = run("real-check", str(self.repo), registry)
        self.assertNotIn("private-fixture-value", json.dumps(result))
        self.assertTrue(result["diagnostics"]["redacted"])
