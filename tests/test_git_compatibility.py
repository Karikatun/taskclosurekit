"""Public Git compatibility and fail-closed control regressions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from taskclosurekit import application
from taskclosurekit._primitives import runner, snapshot

import test_cycle


ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git", path="/usr/bin:/bin")


@unittest.skipUnless(GIT, "System Git required")
class GitCompatibilityTests(unittest.TestCase):
    cli = test_cycle.CycleTests.cli

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tck-git-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                    "GIT_OPTIONAL_LOCKS": "0", "PYTHONDONTWRITEBYTECODE": "1"}
        self.git("init", "--template=", "--initial-branch=master")
        (self.repo / "README.md").write_text("Neutral fixture.\n")
        (self.repo / "sample.txt").write_text("baseline\n")
        self.git("add", ".")
        self.git("commit", "-m", "fixture baseline")
        self.store = self.root / "store"
        self.contract = self.root / "contract.json"

    def git(self, *args):
        return subprocess.run([GIT, *args], cwd=self.repo, env=self.env,
                              capture_output=True, check=True, timeout=15).stdout

    def contract_for_current_repo(self):
        self.contract.write_text(json.dumps({
            "schema": "taskclosurekit/v2", "task": {"id": "git-fixture", "title": "Git compatibility"},
            "repository": str(self.repo), "authority": {"task": {"issuer": "human"},
            "read": ["README.md", "sample.txt"], "write": ["sample.txt"],
            "execution": {"presets": ["git-index-whitespace-v1"]}}, "sources": ["README.md"],
            "acceptance": [{"id": "whitespace", "required": True,
                "evidence": {"all_of": ["git-index-whitespace-v1"]}}],
            "review": {"required": True, "independence": "not_required"},
            "claim": {"type": "configured-acceptance-satisfied"}, "closure": {"authority": "human"}}))

    def prepare(self):
        self.contract_for_current_repo()
        for args in [("task", "create", str(self.contract)), ("authorize",), ("baseline",)]:
            result = self.cli(*args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_normal_packed_clone_reaches_public_baseline_and_diff_check(self):
        clone = self.root / "clone"
        self.git("clone", "--no-local", str(self.repo), str(clone))
        self.repo = clone
        self.assertTrue(list((clone / ".git/objects/pack").glob("*.pack")))
        self.git("fsck", "--strict")
        self.prepare()
        (clone / "sample.txt").write_text("permitted staged change\n")
        self.git("add", "sample.txt")
        result = self.cli("check", "git-index-whitespace-v1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "EVIDENCED")

    def test_linked_worktree_reaches_public_check_review_and_close(self):
        linked = self.root / "linked"
        self.git("repack", "-ad")
        self.git("worktree", "add", "--detach", str(linked), "HEAD")
        self.repo = linked
        self.git("fsck", "--strict")
        self.prepare()
        (linked / "sample.txt").write_text("permitted linked change\n")
        self.git("add", "sample.txt")
        for args in [("check", "git-index-whitespace-v1"), ("review", "--human"), ("close",)]:
            result = self.cli(*args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "CLOSED")

    def assert_blocked(self, reason, action="status"):
        before = {p.name: p.read_bytes() for p in self.store.iterdir()}
        result = self.cli(action)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(reason, json.loads(result.stdout)["reasons"], result.stdout)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.store.iterdir()})

    def test_executable_and_inert_hooks_are_bound_without_execution(self):
        hooks = self.repo / ".git/hooks"
        hooks.mkdir()
        marker = self.root / "hook-ran"
        for name in ("pre-commit", "post-checkout", "post-index-change"):
            script = hooks / name
            script.write_text("#!/bin/sh\nprintf executed > '" + str(marker) + "'\n")
            script.chmod(0o755)
        (hooks / "inert-data").write_text("Inert hook directory entry.\n")
        self.prepare()
        self.assertEqual(self.cli("check", "git-index-whitespace-v1").returncode, 0)
        self.assertFalse(marker.exists(), "inspection/check must not dispatch a hook")
        self.assertNotIn("core.hooksPath", runner.environment().values())
        (hooks / "pre-commit").write_text("Changed executable hook control.\n")
        self.assert_blocked("authority_changed")

    def test_external_canonical_hooks_path_is_bound_without_execution(self):
        hooks = self.root / "external-hooks"
        hooks.mkdir()
        marker = self.root / "external-hook-ran"
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nprintf executed > '" + str(marker) + "'\n")
        hook.chmod(0o755)
        self.git("config", "core.hooksPath", str(hooks))
        self.prepare()
        self.assertEqual(self.cli("check", "git-index-whitespace-v1").returncode, 0)
        self.assertFalse(marker.exists())
        hook.write_text("Changed external hook.\n")
        self.assert_blocked("authority_changed")

    def test_quoted_or_commented_hooks_path_is_an_explicit_blocker(self):
        hooks = self.root / "external-hooks"
        hooks.mkdir()
        config = self.repo / ".git/config"
        initial = config.read_text()
        for value in ('"' + str(hooks) + '"', str(hooks) + " # Git comment", str(hooks) + "\\t"):
            with self.subTest(value=value):
                config.write_text(initial.replace("[core]\n", "[core]\n\thooksPath = " + value + "\n"))
                self.contract_for_current_repo()
                with self.assertRaisesRegex(RuntimeError,"^unsupported_git_config$"):
                    snapshot.capture(self.repo,self.contract,["README.md"])

    def test_linked_metadata_crosslink_mismatch_blocks_before_git_consumer(self):
        linked = self.root / "linked"
        self.git("worktree", "add", "--detach", str(linked), "HEAD")
        self.repo = linked
        self.contract_for_current_repo()
        admin = Path((linked / ".git").read_text().removeprefix("gitdir: ").strip())
        (admin / "gitdir").write_text(str(self.root / "different/.git") + "\n")
        with patch.object(runner, "git_read") as consumer:
            with self.assertRaisesRegex(RuntimeError, "^git_worktree_identity_mismatch$"):
                snapshot.capture(linked, self.contract, ["README.md"])
        consumer.assert_not_called()

    def test_metadata_symlink_escape_blocks_before_git_consumer(self):
        external = self.root / "external-objects"
        external.mkdir()
        shutil.move(str(self.repo / ".git/objects"), str(external / "objects"))
        (self.repo / ".git/objects").symlink_to(external / "objects", target_is_directory=True)
        self.contract_for_current_repo()
        with patch.object(runner, "git_read") as consumer:
            with self.assertRaisesRegex(RuntimeError, "^symlink_unsupported$"):
                snapshot.capture(self.repo, self.contract, ["README.md"])
        consumer.assert_not_called()

    def test_equivalent_git_directory_swap_is_authority_drift(self):
        self.prepare()
        self.assertEqual(self.cli("check", "git-index-whitespace-v1").returncode, 0)
        original = self.root / "previous-git-dir"
        shutil.move(str(self.repo / ".git"), str(original))
        shutil.copytree(original, self.repo / ".git", copy_function=shutil.copy2)
        self.assert_blocked("authority_changed")

    def test_hook_change_during_inspection_is_snapshot_race(self):
        hooks = self.repo / ".git/hooks"
        hooks.mkdir()
        hook = hooks / "pre-commit"
        hook.write_text("Inert initial control.\n")
        self.contract_for_current_repo()
        original = runner.git_read
        changed = False
        def read_with_concurrent_hook_change(*args, **kwargs):
            nonlocal changed
            result = original(*args, **kwargs)
            if not changed:
                changed = True
                hook.write_text("Changed during read-only inspection.\n")
            return result
        with patch.object(runner, "git_read", side_effect=read_with_concurrent_hook_change):
            with self.assertRaisesRegex(RuntimeError, "^snapshot_race$"):
                snapshot.capture(self.repo, self.contract, ["README.md"])
        self.assertTrue(changed)

    def test_index_v4_is_explicitly_unsupported(self):
        self.git("update-index", "--index-version=4")
        self.contract_for_current_repo()
        created = self.cli("task", "create", str(self.contract))
        self.assertEqual(created.returncode, 0, created.stdout)
        self.assert_blocked("unsupported_git_index_format", "baseline")

    def test_split_index_is_explicitly_unsupported(self):
        self.git("update-index", "--split-index")
        self.contract_for_current_repo()
        created = self.cli("task", "create", str(self.contract))
        self.assertEqual(created.returncode, 0, created.stdout)
        self.assert_blocked("unsupported_git_index_extension", "baseline")

    def test_sha256_object_format_has_an_explicit_blocker(self):
        sha_repo = self.root / "sha256"
        sha_repo.mkdir()
        self.repo = sha_repo
        self.git("init", "--template=", "--object-format=sha256", "--initial-branch=master")
        (sha_repo / "README.md").write_text("Neutral SHA256 fixture.\n")
        (sha_repo / "sample.txt").write_text("baseline\n")
        self.git("add", ".")
        self.git("commit", "-m", "SHA256 fixture")
        self.contract_for_current_repo()
        self.assertEqual(self.cli("task", "create", str(self.contract)).returncode, 0)
        self.assert_blocked("unsupported_git_object_format", "baseline")

    def test_pack_checksum_corruption_blocks_without_journal_append(self):
        clone = self.root / "clone"
        self.git("clone", "--no-local", str(self.repo), str(clone))
        self.repo = clone
        self.prepare()
        pack = next((clone / ".git/objects/pack").glob("*.pack"))
        data = bytearray(pack.read_bytes())
        data[-1] ^= 1
        pack.chmod(0o600)  # Disposable clone packs are normally read-only.
        pack.write_bytes(data)
        self.assert_blocked("git_object_identity_mismatch")

    def test_incomplete_pack_pair_is_explicitly_blocked(self):
        clone = self.root / "clone"
        self.git("clone", "--no-local", str(self.repo), str(clone))
        self.repo = clone
        next((clone / ".git/objects/pack").glob("*.idx")).unlink()
        self.contract_for_current_repo()
        self.assertEqual(self.cli("task", "create", str(self.contract)).returncode, 0)
        self.assert_blocked("unsupported_git_object_layout", "baseline")

    def test_configured_external_diff_is_rejected_without_dispatch(self):
        marker = self.root / "diff-ran"
        driver = self.root / "external-diff"
        driver.write_text("#!/bin/sh\nprintf executed > '" + str(marker) + "'\n")
        driver.chmod(0o755)
        self.git("config", "diff.external", str(driver))
        self.contract_for_current_repo()
        self.assertEqual(self.cli("task", "create", str(self.contract)).returncode, 0)
        self.assert_blocked("unsupported_git_config", "baseline")
        self.assertFalse(marker.exists())
