"""Synthetic staged-index fixture; no source project is copied or executed."""
import json
import unittest
import test_v2_cycle as cycle_fixture
import test_namespace as namespace_fixture


class SyntheticTemplateFixtureTests(unittest.TestCase):
    git = cycle_fixture.V2CycleTests.git
    cli = namespace_fixture.NamespaceTests.cli
    successful = namespace_fixture.NamespaceTests.successful

    def setUp(self):
        cycle_fixture.V2CycleTests.setUp(self)
        self.launch = cycle_fixture.ROOT
        self.env["GIT_AUTHOR_NAME"] = "TaskClosureKit Synthetic Fixture"
        self.env["GIT_COMMITTER_NAME"] = "TaskClosureKit Synthetic Fixture"
        self.target = self.repo / "webapp/src/WhitespaceFixture.tsx"
        self.target.parent.mkdir(parents=True)
        self.target.write_text("export const fixtureLabel = 'synthetic';\n")
        self.git("add", "webapp"); self.git("commit", "-m", "synthetic fixture baseline")
        self.value["authority"]["read"] = ["README.md", "webapp"]
        self.value["authority"]["write"] = ["webapp/src/WhitespaceFixture.tsx"]
        self.input.write_text(json.dumps(self.value))

    def test_failed_index_is_blocked_then_corrected_snapshot_closes(self):
        self.successful("task", "create", str(self.input))
        self.successful("authorize", human=True)
        baseline = self.successful("baseline")
        self.target.write_text("export const fixtureLabel = 'synthetic';  \n")
        self.git("add", "webapp/src/WhitespaceFixture.tsx")
        self.target.write_text("export const fixtureLabel = 'synthetic';\n")
        failed = self.cli("check", "git-index-whitespace-v1")
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        failure = json.loads(failed.stdout)
        self.assertEqual(failure["reasons"], ["check_failed"])
        self.assertNotEqual(failure["snapshot"], baseline["snapshot"])
        self.assertEqual(self.git("diff", "--check"), b"")
        blocked = self.cli("close")
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("required_evidence_failed", json.loads(blocked.stdout)["reasons"])
        self.git("add", "webapp/src/WhitespaceFixture.tsx")
        passed = self.successful("check", "git-index-whitespace-v1")
        self.assertNotEqual(passed["snapshot"], failure["snapshot"])
        self.assertEqual(self.git("diff", "--cached", "--check"), b"")
        self.successful("review", "--human", human=True)
        self.assertEqual(self.successful("evaluate")["decision"], "CLAIMABLE")
        closed = self.successful("close", human=True)
        self.assertEqual(closed["state"], "CLOSED")
        self.assertEqual(closed["claim"]["type"], "configured-acceptance-satisfied")
        self.assertEqual(closed["claim"]["snapshot_digest"], passed["snapshot"])


if __name__ == "__main__":
    unittest.main()
