"""The current CLI runs from its own sources; obsolete namespaces stay unavailable."""
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import unittest
import test_v2_cycle as cycle_fixture

ROOT = cycle_fixture.ROOT


class NamespaceTests(unittest.TestCase):
    git = cycle_fixture.V2CycleTests.git

    def setUp(self):
        cycle_fixture.V2CycleTests.setUp(self)
        self.launch = self.root / "launch"
        self.launch.mkdir()
        shutil.copytree(ROOT / "taskclosurekit", self.launch / "taskclosurekit")
        (self.launch / "tests").mkdir()
        (self.launch / "tests" / "fixture.py").write_text("# Bound distribution test input.\n")

    def cli(self, *args, human=False):
        command = [sys.executable, "-B", "-m", "taskclosurekit", "--store", str(self.store), *args, "--json"]
        if not human:
            return subprocess.run(command, cwd=self.launch, env=self.env, capture_output=True, text=True, timeout=15)
        import pty
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(command, cwd=self.launch, env=self.env, stdin=slave,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            os.close(slave); slave = None
            ready, _, _ = select.select([process.stderr], [], [], 10)
            self.assertTrue(ready, "confirmation prompt was not reached")
            prompt = process.stderr.readline()
            match = re.search(r"([0-9a-f]{64})\n$", prompt)
            self.assertIsNotNone(match, prompt)
            os.write(master, (match.group(1) + "\n").encode())
            stdout, stderr = process.communicate(timeout=15)
            return subprocess.CompletedProcess(command, process.returncode, stdout, prompt + stderr)
        finally:
            if slave is not None: os.close(slave)
            os.close(master)

    def successful(self, *args, human=False):
        result = self.cli(*args, human=human)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_current_cli_closes_without_a_sibling_package(self):
        self.assertEqual(self.successful("task", "create", str(self.input))["state"], "DRAFT")
        self.successful("authorize", human=True)
        self.successful("baseline")
        target = self.repo / "src/foo.py"
        target.write_text("value = 2  \n"); self.git("add", "src/foo.py")
        failed = self.cli("check", "git-index-whitespace-v1")
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertIn("check_failed", json.loads(failed.stdout)["reasons"])
        target.write_text("value = 2\n"); self.git("add", "src/foo.py")
        self.successful("check", "git-index-whitespace-v1")
        self.successful("review", "--human", human=True)
        self.assertEqual(self.successful("evaluate")["decision"], "CLAIMABLE")
        self.assertEqual(self.cli("close").returncode, 1)
        self.assertEqual(self.successful("close", human=True)["state"], "CLOSED")
        before = {p.name: p.read_bytes() for p in self.store.iterdir()}
        self.assertEqual(self.successful("status", "--next")["state"], "CLOSED")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.store.iterdir()})

    def test_obsolete_namespace_is_unavailable(self):
        result = subprocess.run([sys.executable, "-B", "-S", "-c",
                                 "import importlib.util; assert importlib.util.find_spec('taskproof') is None"],
                                cwd=ROOT, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_previous_task_format_and_lifecycle_fail_closed(self):
        self.input.write_text(json.dumps({"schema": 1, "run_id": "old-task", "repo": str(self.repo)}))
        rejected = self.cli("task", "create", str(self.input))
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(json.loads(rejected.stdout)["reasons"], ["unknown_or_missing_fields"])
        self.assertFalse(self.store.exists())
        from taskclosurekit._primitives.store import Store
        store = Store(self.store, self.repo)
        with store.locked(create=True):
            store.initialize()
            store.append([], "DRAFT", {"contract": {"schema": 1}}, "old-task")
        before = {p.name: p.read_bytes() for p in self.store.iterdir()}
        rejected = self.cli("status", "--next")
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(json.loads(rejected.stdout)["reasons"], ["invalid_initial_state"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.store.iterdir()})

    def test_private_program_and_tests_drift_block_without_refreshing_store(self):
        self.successful("task", "create", str(self.input))
        self.successful("authorize", human=True)
        self.successful("baseline")
        self.successful("check", "git-index-whitespace-v1")
        before = {p.name: p.read_bytes() for p in self.store.iterdir()}
        for relative in ("taskclosurekit/_primitives/policy.py", "taskclosurekit/_primitives/store.py", "tests/fixture.py"):
            with self.subTest(relative=relative):
                path = self.launch / relative
                original = path.read_bytes()
                path.write_bytes(original + b"\n# Changed execution input.\n")
                try:
                    stale = self.cli("status", "--next")
                    self.assertEqual(stale.returncode, 4, stale.stdout + stale.stderr)
                    value = json.loads(stale.stdout)
                    self.assertIn("authority_changed", value["reasons"])
                    self.assertEqual(value["freshness"][0]["state"], "STALE_ENVIRONMENT")
                    self.assertEqual(value["next_action"], "new_task")
                    self.assertEqual(before, {p.name: p.read_bytes() for p in self.store.iterdir()})
                    self.assertEqual(self.cli("check", "git-index-whitespace-v1").returncode, 4)
                    self.assertEqual(before, {p.name: p.read_bytes() for p in self.store.iterdir()})
                finally:
                    path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
