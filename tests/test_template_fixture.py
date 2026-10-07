"""Synthetic first-integration fixture; it does not copy or run a source project."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")


@unittest.skipUnless(GIT, "git is required for the generated integration fixture")
class SyntheticTemplateFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "synthetic-template"
        self.repo.mkdir()
        self.store = self.root / "evidence-store"
        self.contract_path = self.root / "contract.json"
        self.review_path = self.root / "review.json"
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "TaskProof Synthetic Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "TaskProof Synthetic Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_OPTIONAL_LOCKS": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.git("init", "--template=", "--initial-branch=master")
        (self.repo / "README.md").write_text("Synthetic fixture. No source project copied.\n")
        app_file = self.repo / "webapp/src/TaskProofWhitespaceFixture.tsx"
        app_file.parent.mkdir(parents=True)
        app_file.write_text("export const fixtureLabel = 'synthetic';\n")
        self.git("add", "README.md", "webapp/src/TaskProofWhitespaceFixture.tsx")
        self.git("commit", "-m", "synthetic fixture baseline")
        self.contract_path.write_text(json.dumps({
            "schema": 1,
            "run_id": "synthetic-first-integration",
            "repo": str(self.repo),
            "mode": "Direct",
            "scope": ["webapp/src/TaskProofWhitespaceFixture.tsx"],
            "actions": ["snapshot", "check", "review", "close"],
            "sources": ["README.md"],
            "preset": "git-index-whitespace-v1",
            "acceptance": ["staged-whitespace"],
            "review": {"required": True, "independence": "not_required"},
        }))

    def git(self, *args):
        return subprocess.run([GIT, *args], cwd=self.repo, env=self.env,
                              capture_output=True, check=True).stdout

    def git_result(self, *args):
        return subprocess.run([GIT, *args], cwd=self.repo, env=self.env,
                              capture_output=True)

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "taskproof", "--store", str(self.store), *args],
            cwd=ROOT, env=self.env, capture_output=True, text=True,
        )

    def result(self, *args):
        completed = self.cli(*args)
        self.assertEqual(completed.stderr, "")
        return completed.returncode, json.loads(completed.stdout)

    def test_failed_index_is_blocked_then_corrected_snapshot_closes(self):
        created = self.cli("contract", str(self.contract_path))
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        self.assertEqual(json.loads(created.stdout)["state"], "DRAFT")
        code, baseline = self.result("baseline")
        self.assertEqual(code, 0)
        self.assertEqual(baseline["state"], "BASELINED")

        app_file = self.repo / "webapp/src/TaskProofWhitespaceFixture.tsx"
        app_file.write_text("export const fixtureLabel = 'synthetic';  \n")
        self.git("add", "webapp/src/TaskProofWhitespaceFixture.tsx")
        # The worktree now has a clean version; the staged index still contains the defect.
        app_file.write_text("export const fixtureLabel = 'synthetic';\n")
        code, failed = self.result("check")
        self.assertEqual((code, failed["state"], failed["reason"]), (1, "BLOCKED", "check_failed"))
        self.assertEqual(self.git_result("diff", "--check").returncode, 0)
        self.assertEqual(self.git_result("diff", "--cached", "--check").returncode, 2)
        self.assertNotEqual(failed["snapshot"], baseline["snapshot"])

        code, blocked_close = self.result("close")
        self.assertEqual((code, blocked_close["state"], blocked_close["reason"]),
                         (1, "BLOCKED", "check_failed"))

        self.git("add", "webapp/src/TaskProofWhitespaceFixture.tsx")
        code, passed = self.result("check")
        self.assertEqual((code, passed["state"], passed["reason"]), (0, "CHECKED", None))
        self.assertNotEqual(passed["snapshot"], failed["snapshot"])
        self.assertEqual(self.git("diff", "--cached", "--check"), b"")

        self.review_path.write_text(json.dumps({
            "schema": 1,
            "run_id": "synthetic-first-integration",
            "snapshot": passed["snapshot"],
            "decision": "approve",
        }))
        code, reviewed = self.result("review", str(self.review_path))
        self.assertEqual((code, reviewed["state"], reviewed["independence"]),
                         (0, "REVIEWED", "not_required"))
        code, closed = self.result("close")
        self.assertEqual((code, closed["state"], closed["claim"]),
                         (0, "CLOSED", "staged-whitespace"))


if __name__ == "__main__":
    unittest.main()
