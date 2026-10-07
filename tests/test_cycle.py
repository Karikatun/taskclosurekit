import json
import hashlib
import hmac
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import unicodedata
import zlib
from unittest.mock import patch

from taskproof import core, runner, snapshot
from taskproof.store import Store


ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                    "GIT_OPTIONAL_LOCKS": "0", "PYTHONDONTWRITEBYTECODE": "1"}
        self.git("init", "--template=", "--initial-branch=master")
        (self.repo / "sample.txt").write_text("baseline\n")
        (self.repo / "AGENTS.md").write_text("Fixture rules are data.\n")
        self.git("add", "sample.txt", "AGENTS.md")
        self.git("commit", "-m", "fixture baseline")
        self.store = self.root / "store"
        self.contract = self.root / "contract.json"
        self.contract.write_text(json.dumps({
            "schema": 1, "run_id": "fixture", "repo": str(self.repo),
            "mode": "Direct", "scope": ["sample.txt"],
            "actions": ["snapshot", "check", "review", "close"],
            "sources": ["AGENTS.md"], "preset": "git-index-whitespace-v1",
            "acceptance": ["staged-whitespace"],
            "review": {"required": True, "independence": "not_required"}}))

    def git(self, *args):
        return subprocess.run([GIT, *args], cwd=self.repo, env=self.env,
                              capture_output=True, check=True).stdout

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "taskproof", "--store", str(self.store), *args],
                              cwd=getattr(self, "launch_root", ROOT), env=self.env, capture_output=True, text=True)

    def result(self, *args):
        result = self.cli(*args)
        self.assertFalse(result.stderr, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def contract_change(self, **changes):
        value = json.loads(self.contract.read_text())
        value.update(changes)
        self.contract.write_text(json.dumps(value))

    def checked(self):
        self.start()
        code, result = self.result("check")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["state"], "CHECKED")
        return result["snapshot"]

    def test_git_object_info_metadata_rejected_before_check(self):
        self.start()
        before = self.store_bytes()
        note = self.repo / ".git" / "objects" / "info" / "fixture-note.txt"
        note.write_text("Synthetic inert metadata.\n")
        code, result = self.result("check")
        self.assertEqual((code, result.get("reason")), (1, "unsupported_git_object_layout"), result)
        self.assertEqual(self.store_bytes(), before, "unsupported input must not append RUNNING or evidence")

    def test_git_object_extra_directory_rejected_before_baseline(self):
        (self.repo / ".git" / "objects" / "fixture-extra").mkdir()
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        before = self.store_bytes()
        code, result = self.result("baseline")
        self.assertEqual((code, result.get("reason")), (1, "unsupported_git_object_layout"), result)
        self.assertEqual(self.store_bytes(), before)

    def test_git_packed_repository_rejected_before_baseline(self):
        # Git produces an ordinary healthy pack; no malformed objects or payloads.
        self.git("repack", "-a")
        self.assertTrue(list((self.repo / ".git" / "objects" / "pack").glob("*.pack")))
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        before = self.store_bytes()
        code, result = self.result("baseline")
        self.assertEqual((code, result.get("reason")), (1, "unsupported_git_object_layout"), result)
        self.assertEqual(self.store_bytes(), before)

    def healthy_blob(self, body):
        result = subprocess.run([GIT, "hash-object", "-w", "--stdin"], input=body,
                                cwd=self.repo, env=self.env, capture_output=True, check=True)
        oid = result.stdout.decode("ascii").strip()
        return oid, (self.repo / ".git" / "objects" / oid[:2] / oid[2:]).read_bytes()

    def test_git_object_healthy_types_and_new_staged_objects_close(self):
        self.git("tag", "-a", "fixture-tag", "-m", "Synthetic healthy tag")
        self.start()
        (self.repo / "sample.txt").write_text("ordinary staged change\n")
        self.git("add", "sample.txt")
        self.git("write-tree")  # Another healthy loose type; refs remain frozen.
        unused, compressed = self.healthy_blob(b"Synthetic unused blob.\n")
        snapshot.validate_loose_bytes(compressed, unused, snapshot.Budget())
        code, result = self.result("check")
        self.assertEqual((code, result["state"]), (0, "CHECKED"), result)
        self.assertEqual(self.review(result["snapshot"])[0], 0)
        self.assertEqual(self.result("close")[1]["state"], "CLOSED")

    def test_git_object_unsupported_layout_precedes_git_consumers(self):
        (self.repo / ".git" / "objects" / "info" / "fixture-note.txt").write_text("Inert fixture.\n")
        with patch.object(runner, "git_read") as consumer:
            with self.assertRaisesRegex(RuntimeError, "^unsupported_git_object_layout$"):
                snapshot.capture(self.repo, self.contract, ["AGENTS.md"])
        consumer.assert_not_called()

    def test_git_object_noncanonical_fanout_is_unsupported(self):
        # Empty metadata directory only; no misnamed or malformed object bytes.
        (self.repo / ".git" / "objects" / "ZZ").mkdir()
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        self.rejected("baseline", "unsupported_git_object_layout")

    def test_git_object_healthy_large_body_rejected_before_git_consumers(self):
        self.healthy_blob(b"x" * (snapshot.FILE_LIMIT + 1))
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        before = self.store_bytes()
        self.rejected("baseline", "git_object_limit")
        self.assertEqual(self.store_bytes(), before)
        with patch.object(runner, "git_read") as consumer:
            with self.assertRaisesRegex(RuntimeError, "^git_object_limit$"):
                snapshot.capture(self.repo, self.contract, ["AGENTS.md"])
        consumer.assert_not_called()

    def test_git_object_healthy_body_aggregate_limit_at_validator_seam(self):
        oid, compressed = self.healthy_blob(b"x" * 1024)
        budget = snapshot.Budget()
        budget.bytes = snapshot.AGGREGATE_LIMIT - 100
        with self.assertRaisesRegex(RuntimeError, "^snapshot_limit$"):
            snapshot.validate_loose_bytes(compressed, oid, budget)
        self.assertLess(budget.bytes, snapshot.AGGREGATE_LIMIT,
                        "declared body must be rejected before inflating the remainder")

    def test_git_object_healthy_body_time_limit_at_validator_seam(self):
        oid, compressed = self.healthy_blob(b"Fixture body.\n")
        budget = snapshot.Budget()
        budget.started -= snapshot.SNAPSHOT_SECONDS + 1
        with self.assertRaisesRegex(RuntimeError, "^snapshot_limit$"):
            snapshot.validate_loose_bytes(compressed, oid, budget)
        self.assertEqual(budget.bytes, 0)

    def test_git_object_healthy_body_inflation_is_chunk_bounded(self):
        oid, compressed = self.healthy_blob(b"x" * 200000)
        inflater = zlib.decompressobj()
        calls = []

        class ObservedInflater:
            def decompress(self, data, max_length=0):
                calls.append(max_length)
                return inflater.decompress(data, max_length)

            def __getattr__(self, name):
                return getattr(inflater, name)

        with patch.object(snapshot.zlib, "decompressobj", return_value=ObservedInflater()):
            snapshot.validate_loose_bytes(compressed, oid, snapshot.Budget())
        self.assertGreater(len(calls), 2)
        self.assertEqual(calls[0], snapshot.OBJECT_HEADER_LIMIT + 1)
        self.assertTrue(all(0 < amount <= 65536 for amount in calls))

    def test_git_object_healthy_addition_during_snapshot_is_rejected(self):
        self.start()
        before = self.store_bytes()
        original = snapshot.regular
        changed = False

        def add_healthy_object(path, budget, *args, **kwargs):
            nonlocal changed
            result = original(path, budget, *args, **kwargs)
            if not changed and kwargs.get("content") and kwargs.get("dir_fd") is not None:
                changed = True
                self.healthy_blob(b"Synthetic concurrent healthy addition.\n")
            return result

        with patch.object(snapshot, "regular", side_effect=add_healthy_object):
            with self.assertRaisesRegex(RuntimeError, "^snapshot_race$"):
                core.execute(self.store, "check")
        self.assertTrue(changed)
        self.assertEqual(self.store_bytes(), before)

    def review(self, identity=None, **changes):
        if identity is None:
            identity = self.checked()
        value = {"schema": 1, "run_id": "fixture", "snapshot": identity, "decision": "approve"}
        value.update(changes)
        path = self.root / "review.json"
        path.write_text(json.dumps(value))
        return self.result("review", str(path))

    def rejected(self, action, reason):
        code, result = self.result(action)
        self.assertNotEqual(code, 0)
        self.assertEqual(result["reason"], reason, result)

    def store_bytes(self):
        return {path.name: path.read_bytes() for path in self.store.iterdir()}

    def start(self):
        result = self.cli("contract", str(self.contract))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.cli("baseline")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def attributes_fixture(self, attributes=None):
        app = self.repo / "app"
        app.mkdir()
        (app / "sample.txt").write_text("baseline\n")
        if attributes is not None:
            (app / ".gitattributes").write_text(attributes)
        self.git("add", "app")
        self.git("commit", "-m", "fixture nested baseline")
        self.contract_change(scope=["app"])
        return app

    def source_fixture(self, tracked=True):
        docs = self.repo / "docs"
        docs.mkdir()
        source = docs / "Rules.md"
        source.write_text("Fixture source rules.\n")
        if tracked:
            self.git("add", "docs/Rules.md")
            self.git("commit", "-m", "fixture source baseline")
        self.contract_change(scope=["docs", "sample.txt"], sources=["docs/Rules.md"])
        return source

    def case_path_alias(self, path):
        alias = path.with_name(path.name.swapcase())
        if alias == path or not alias.exists() or not os.path.samefile(path, alias):
            self.skipTest("filesystem does not resolve this existing-component case alias")
        return alias

    def test_boundary_store_parent_alias_rejected_without_writes(self):
        alias = self.case_path_alias(self.repo)
        self.store = alias / "evidence"
        code, result = self.result("contract", str(self.contract))
        self.assertEqual((code, result.get("state")), (1, "BLOCKED"), result)
        self.assertFalse((self.repo / "evidence").exists(), "forbidden store must not be created")

    def test_boundary_repo_final_alias_rejected_without_store_writes(self):
        alias = self.case_path_alias(self.repo)
        self.contract_change(repo=str(alias))
        code, result = self.result("contract", str(self.contract))
        self.assertEqual((code, result.get("reason")), (1, "noncanonical_path"))
        self.assertFalse(self.store.exists())

    def test_boundary_store_final_alias_resume_is_rejected_without_writes(self):
        self.start()
        before = self.store_bytes()
        self.store = self.case_path_alias(self.store)
        code, result = self.result("resume")
        self.assertEqual((code, result.get("reason")), (1, "noncanonical_path"))
        self.assertEqual(before, self.store_bytes())

    def test_boundary_contract_repo_parent_alias_rejected_before_json_read(self):
        alias = self.case_path_alias(self.repo)
        path = self.repo / "input.json"
        path.write_text("invalid synthetic JSON")
        code, result = self.result("contract", str(alias / path.name))
        self.assertEqual((code, result.get("reason")), (1, "noncanonical_path"))
        self.assertFalse(self.store.exists())

    def test_boundary_contract_store_parent_alias_rejected_before_json_read(self):
        self.store.mkdir(mode=0o700)
        path = self.store / "input.json"
        path.write_text("invalid synthetic JSON")
        before = self.store_bytes()
        alias = self.case_path_alias(self.store)
        code, result = self.result("contract", str(alias / path.name))
        self.assertEqual((code, result.get("reason")), (1, "noncanonical_path"))
        self.assertEqual(before, self.store_bytes())

    def test_boundary_contract_final_alias_rejected_before_json_read(self):
        self.contract.write_text("invalid synthetic JSON")
        alias = self.case_path_alias(self.contract)
        code, result = self.result("contract", str(alias))
        self.assertEqual((code, result.get("reason")), (1, "noncanonical_path"))
        self.assertFalse(self.store.exists())

    def test_boundary_review_repo_parent_alias_rejected_before_json_read(self):
        alias = self.case_path_alias(self.repo)
        path = self.repo / "review.json"
        path.write_text("invalid synthetic JSON")
        self.checked()
        before = self.store_bytes()
        code, result = self.result("review", str(alias / path.name))
        self.assertEqual((code, result.get("reason")), (1, "noncanonical_path"))
        self.assertEqual(before, self.store_bytes())

    def test_boundary_review_store_parent_alias_rejected_before_json_read(self):
        self.checked()
        alias = self.case_path_alias(self.store)
        before = self.store_bytes()
        # No new file inside the evidence store: the first event is valid JSON but
        # has a different schema, so noncanonical_path proves parsing did not occur.
        code, result = self.result("review", str(alias / "0001.json"))
        self.assertEqual((code, result.get("reason")), (1, "noncanonical_path"))
        self.assertEqual(before, self.store_bytes())

    def test_boundary_review_final_alias_rejected_before_json_read(self):
        self.checked()
        path = self.root / "review.json"
        path.write_text("invalid synthetic JSON")
        before = self.store_bytes()
        code, result = self.result("review", str(self.case_path_alias(path)))
        self.assertEqual((code, result.get("reason")), (1, "noncanonical_path"))
        self.assertEqual(before, self.store_bytes())

    def test_boundary_canonical_forbidden_store_has_no_side_effects(self):
        self.store = self.repo / "evidence"
        code, result = self.result("contract", str(self.contract))
        self.assertEqual((code, result.get("reason")), (1, "store_must_be_outside_repository"))
        self.assertFalse(self.store.exists())

    def test_boundary_canonical_contract_repo_has_no_store_side_effects(self):
        path = self.repo / "input.json"
        path.write_bytes(self.contract.read_bytes())
        code, result = self.result("contract", str(path))
        self.assertEqual((code, result.get("reason")), (1, "contract_input_must_be_outside_repository"))
        self.assertFalse(self.store.exists())

    def test_boundary_canonical_contract_store_has_no_store_side_effects(self):
        self.store.mkdir(mode=0o700)
        path = self.store / "input.json"
        path.write_bytes(self.contract.read_bytes())
        before = self.store_bytes()
        code, result = self.result("contract", str(path))
        self.assertEqual((code, result.get("reason")), (1, "contract_input_must_be_outside_store"))
        self.assertEqual(before, self.store_bytes())

    def test_boundary_canonical_review_repo_rejected_before_json_read(self):
        path = self.repo / "review.json"
        path.write_text("invalid synthetic JSON")
        self.checked()
        before = self.store_bytes()
        code, result = self.result("review", str(path))
        self.assertEqual((code, result.get("reason")), (1, "review_input_must_be_outside_repository"))
        self.assertEqual(before, self.store_bytes())

    def test_boundary_canonical_review_store_rejected_before_json_read(self):
        self.checked()
        before = self.store_bytes()
        code, result = self.result("review", str(self.store / "0001.json"))
        self.assertEqual((code, result.get("reason")), (1, "review_input_must_be_outside_store"))
        self.assertEqual(before, self.store_bytes())

    def test_boundary_unicode_parent_alias_rejected_without_store_writes(self):
        renamed = self.root / "r\u00e9po"
        self.repo.rename(renamed)
        self.repo = next(path for path in self.root.iterdir()
                         if path.is_dir() and os.path.samefile(path, renamed))
        alternate = unicodedata.normalize("NFD", self.repo.name)
        if alternate == self.repo.name:
            alternate = unicodedata.normalize("NFC", self.repo.name)
        alias = self.repo.with_name(alternate)
        if alias == self.repo or not alias.exists() or not os.path.samefile(alias, self.repo):
            self.skipTest("filesystem does not resolve this Unicode normalization alias")
        self.contract_change(repo=str(self.repo))
        self.store = alias / "evidence"
        code, result = self.result("contract", str(self.contract))
        self.assertEqual((code, result.get("reason")), (1, "noncanonical_path"))
        self.assertFalse((self.repo / "evidence").exists())

    def test_boundary_canonical_new_outside_paths_complete_cycle(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.store = outside / "new-store"
        self.contract = outside / "new-contract.json"
        self.contract.write_bytes((self.root / "contract.json").read_bytes())
        identity = self.checked()
        review_path = outside / "new-review.json"
        review_path.write_text(json.dumps({"schema": 1, "run_id": "fixture", "snapshot": identity, "decision": "approve"}))
        self.assertEqual(self.result("review", str(review_path))[0], 0)
        self.assertEqual(self.result("close")[1]["state"], "CLOSED")
        before = self.store_bytes()
        self.assertEqual(self.result("resume")[1]["state"], "CLOSED")
        self.assertEqual(before, self.store_bytes())

    def test_boundary_parent_lookup_limit_blocks_before_json_read_or_writes(self):
        directory = self.root / "many-entries"
        directory.mkdir()
        path = directory / "present.json"
        path.write_text("invalid synthetic JSON")
        alias = self.case_path_alias(path)
        for index in range(10001):
            (directory / ("entry-%05d" % index)).touch()
        code, result = self.result("contract", str(alias))
        self.assertEqual((code, result.get("reason")), (1, "snapshot_limit"))
        self.assertFalse(self.store.exists())

    def test_source_case_alias_rejected_before_trusted_baseline(self):
        source = self.source_fixture()
        alias = self.repo / "docs" / "rules.md"
        if not alias.is_file() or not os.path.samefile(source, alias):
            self.skipTest("filesystem does not resolve the source case alias")
        self.contract_change(sources=["docs/rules.md"])
        code, result = self.result("contract", str(self.contract))
        if code == 0:
            before = self.store_bytes()
            self.rejected("baseline", "invalid_source_binding")
            self.assertEqual(before, self.store_bytes())
        else:
            self.assertEqual(result["reason"], "invalid_source_binding")
            self.assertFalse(self.store.exists())

    def test_source_index_only_modification_is_protected_inside_scope(self):
        source = self.source_fixture()
        baseline_rules = source.read_bytes()
        self.start()
        before = self.store_bytes()
        source.write_text("Changed fixture source.\n")
        self.git("add", "docs/Rules.md")
        source.write_bytes(baseline_rules)
        self.rejected("check", "source_changed")
        self.assertEqual(before, self.store_bytes())

    def test_exact_source_worktree_modification_is_protected_inside_scope(self):
        source = self.source_fixture()
        self.start()
        source.write_text("Changed fixture source.\n")
        self.rejected("check", "source_changed")

    def test_source_index_only_removal_is_protected_inside_scope(self):
        source = self.source_fixture()
        self.start()
        self.git("update-index", "--force-remove", "docs/Rules.md")
        self.assertTrue(source.is_file())
        self.rejected("check", "source_changed")

    def test_source_index_only_mode_change_is_protected_inside_scope(self):
        self.source_fixture()
        self.start()
        self.git("update-index", "--chmod=+x", "docs/Rules.md")
        self.rejected("check", "source_changed")

    def test_untracked_source_index_addition_is_protected_inside_scope(self):
        self.source_fixture(tracked=False)
        self.start()
        self.git("add", "docs/Rules.md")
        self.rejected("check", "source_changed")

    def test_missing_source_before_baseline_cannot_be_trusted(self):
        source = self.source_fixture()
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        before = self.store_bytes()
        source.unlink()
        self.rejected("baseline", "missing_source")
        self.assertEqual(before, self.store_bytes())

    def test_canonical_source_normal_code_cycle_closes(self):
        self.source_fixture()
        self.start()
        (self.repo / "sample.txt").write_text("approved change\n")
        self.git("add", "sample.txt")
        code, result = self.result("check")
        self.assertEqual((code, result["state"]), (0, "CHECKED"))
        self.assertEqual(self.review(result["snapshot"])[0], 0)
        self.assertEqual(self.result("close")[1]["state"], "CLOSED")

    def test_source_index_case_alias_addition_is_rejected(self):
        self.source_fixture()
        self.start()
        object_id = self.git("rev-parse", "HEAD:docs/Rules.md").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "100644," + object_id + ",docs/rules.md")
        self.assertIn(b"docs/rules.md", self.git("ls-files", "--stage"))
        self.rejected("check", "invalid_source_binding")

    def test_source_index_case_alias_baseline_is_rejected(self):
        self.source_fixture()
        object_id = self.git("rev-parse", "HEAD:docs/Rules.md").decode().strip()
        self.git("update-index", "--force-remove", "docs/Rules.md")
        self.git("update-index", "--add", "--cacheinfo", "100644," + object_id + ",docs/rules.md")
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        self.rejected("baseline", "invalid_source_binding")

    def test_source_index_filesystem_alias_rejected_before_baseline(self):
        docs = self.repo / "docs"
        docs.mkdir()
        source = docs / "R\u00e9gles.md"
        source.write_text("Fixture source rules.\n")
        alias = docs / "Re\u0301gles.md"
        if not alias.is_file() or not os.path.samefile(source, alias):
            self.skipTest("filesystem does not resolve the source Unicode alias")
        # Use the actual inventory spelling for the mandatory source.
        actual = next(path.name for path in docs.iterdir())
        alternate = "Re\u0301gles.md" if actual != "Re\u0301gles.md" else "R\u00e9gles.md"
        self.git("config", "core.precomposeunicode", "false")
        self.contract_change(scope=["docs", "sample.txt"], sources=["docs/" + actual])
        object_id = self.git("hash-object", "-w", str(source)).decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "100644," + object_id + ",docs/" + alternate)
        self.assertIn(("docs/" + alternate).encode(), self.git("ls-files", "--stage", "-z"))
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        self.rejected("baseline", "invalid_source_binding")

    def test_source_index_alias_change_after_failed_check_preserves_receipt(self):
        source = self.source_fixture()
        self.start()
        (self.repo / "sample.txt").write_text("bad  \n")
        self.git("add", "sample.txt")
        self.assertEqual(self.result("check")[1]["reason"], "check_failed")
        before = self.store_bytes()
        object_id = self.git("rev-parse", "HEAD:docs/Rules.md").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "100644," + object_id + ",docs/rules.md")
        source.write_text("Changed fixture source.\n")
        self.rejected("check", "invalid_source_binding")
        self.rejected("close", "invalid_source_binding")
        self.assertEqual(before, self.store_bytes())

    def test_persisted_missing_source_binding_is_rejected(self):
        self.source_fixture()
        self.start()
        path = self.store / "0002.json"
        original = path.read_bytes()
        key = (self.store / "key").read_bytes()
        for field in ("source_bindings", "entries"):
            with self.subTest(field=field):
                event = json.loads(original)
                event["payload"]["snapshot"][field].pop("docs/Rules.md")
                event["payload"]["digest"] = snapshot.digest(event["payload"]["snapshot"])
                body = {name: item for name, item in event.items() if name != "signature"}
                event["signature"] = hmac.new(key, snapshot.canonical(body), hashlib.sha256).hexdigest()
                path.write_bytes(snapshot.canonical(event))
                before = self.store_bytes()
                self.rejected("check", "invalid_source_binding")
                self.rejected("resume", "invalid_source_binding")
                self.assertEqual(before, self.store_bytes())
                path.write_bytes(original)

    def test_persisted_source_index_mismatch_is_rejected(self):
        self.source_fixture()
        self.start()
        path = self.store / "0002.json"
        event = json.loads(path.read_bytes())
        event["payload"]["snapshot"]["source_bindings"]["docs/Rules.md"]["index_entries"] = {}
        event["payload"]["digest"] = snapshot.digest(event["payload"]["snapshot"])
        key = (self.store / "key").read_bytes()
        body = {name: item for name, item in event.items() if name != "signature"}
        event["signature"] = hmac.new(key, snapshot.canonical(body), hashlib.sha256).hexdigest()
        path.write_bytes(snapshot.canonical(event))
        before = self.store_bytes()
        self.rejected("check", "invalid_source_binding")
        self.assertEqual(before, self.store_bytes())

    def test_close_rejects_missing_check(self):
        self.start()
        result = self.cli("close")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["reason"], "missing_check")

    def test_actual_success_cycle_and_readonly_resume(self):
        self.start()
        (self.repo / "sample.txt").write_text("approved change\n")
        self.git("add", "sample.txt")
        code, result = self.result("check")
        self.assertEqual((code, result["state"]), (0, "CHECKED"))
        self.assertEqual(self.review(result["snapshot"])[0], 0)
        self.assertEqual(self.result("close")[1]["claim"], "staged-whitespace")
        before = self.store_bytes()
        with patch.object(runner, "run_check", side_effect=AssertionError("resume must not execute")):
            resumed = core.execute(self.store, "resume")
        self.assertEqual(resumed["state"], "CLOSED")
        self.assertEqual(before, self.store_bytes())

    def test_skipped_states(self):
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        self.rejected("check", "missing_baseline")
        self.rejected("close", "missing_baseline")
        self.assertEqual(self.result("baseline")[0], 0)
        self.rejected("close", "missing_check")
        self.assertEqual(self.result("check")[0], 0)
        self.rejected("close", "missing_review")

    def test_real_whitespace_failure_and_output_discard(self):
        self.start()
        marker = "SYNTHETIC_SECRET_FIXTURE_123"
        (self.repo / "sample.txt").write_text(marker + "  \n")
        self.git("add", "sample.txt")
        code, result = self.result("check")
        self.assertEqual((code, result["reason"]), (1, "check_failed"))
        self.rejected("close", "check_failed")
        for data in self.store_bytes().values():
            self.assertNotIn(marker.encode(), data)
        event = json.loads((self.store / "0004.json").read_text())
        self.assertGreater(event["payload"]["journal"]["stdout_bytes"], 0)
        self.assertEqual(event["payload"]["journal"]["kind"], "discarded-output-v1")

    def test_resume_completed_failed_check_advises_same_run_retry(self):
        self.start()
        (self.repo / "sample.txt").write_text("ordinary failed whitespace  \n")
        self.git("add", "sample.txt")
        code, failed = self.result("check")
        self.assertEqual((code, failed["reason"], failed["next"]),
                         (1, "check_failed", "inspect_then_check"))
        failed_store = self.store_bytes()
        code, resumed = self.result("resume")
        self.assertEqual((code, resumed["state"], resumed["last_confirmed"],
                          resumed["reason"], resumed["next"]),
                         (1, "BLOCKED", "BASELINED", "check_failed", "inspect_then_check"))
        self.assertEqual(self.store_bytes(), failed_store)
        with patch.object(runner, "run_check", side_effect=AssertionError("resume must not repeat check")) as execution:
            self.assertEqual(core.execute(self.store, "resume")["next"], "inspect_then_check")
        execution.assert_not_called()
        self.assertEqual(self.store_bytes(), failed_store)
        (self.repo / "sample.txt").write_text("ordinary corrected whitespace\n")
        self.git("add", "sample.txt")
        code, resumed = self.result("resume")
        self.assertEqual((code, resumed["reason"], resumed["next"]),
                         (1, "check_failed", "inspect_then_check"))
        self.assertEqual(self.store_bytes(), failed_store)
        code, checked = self.result("check")
        self.assertEqual((code, checked["state"]), (0, "CHECKED"))
        self.assertEqual(self.review(checked["snapshot"])[0], 0)
        self.assertEqual(self.result("close")[1]["state"], "CLOSED")
        current = self.store_bytes()
        self.assertTrue(all(current[name] == data for name, data in failed_store.items()))
        self.assertEqual(self.result("resume")[1]["next"], "none")
        self.assertEqual(self.store_bytes(), current)

    def test_resume_completed_failed_check_after_previous_success_advises_retry(self):
        self.checked()
        (self.repo / "sample.txt").write_text("ordinary later failure  \n")
        self.git("add", "sample.txt")
        self.assertEqual(self.result("check")[1]["reason"], "check_failed")
        before = self.store_bytes()
        code, resumed = self.result("resume")
        self.assertEqual((code, resumed["last_confirmed"], resumed["next"]),
                         (1, "CHECKED", "inspect_then_check"))
        self.assertEqual(self.store_bytes(), before)
        (self.repo / "sample.txt").write_text("ordinary later correction\n")
        self.git("add", "sample.txt")
        self.assertEqual(self.result("resume")[1]["next"], "inspect_then_check")
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(self.result("check")[1]["state"], "CHECKED")

    def test_resume_completed_failed_check_with_outside_scope_drift_needs_new_run(self):
        self.start()
        (self.repo / "sample.txt").write_text("ordinary failed whitespace  \n")
        self.git("add", "sample.txt")
        self.assertEqual(self.result("check")[1]["reason"], "check_failed")
        before = self.store_bytes()
        (self.repo / "outside-scope.txt").write_text("Synthetic ordinary drift.\n")
        code, resumed = self.result("resume")
        self.assertEqual((code, resumed["next"]), (1, "inspect_then_new_run"))
        self.rejected("check", "outside_scope_changed")
        self.assertEqual(self.store_bytes(), before)

    def test_resume_inputs_changed_during_completed_check_remains_conservative(self):
        self.start()
        original = runner.run_check

        def completed_with_ordinary_drift(*args):
            result = original(*args)
            (self.repo / "sample.txt").write_text("Synthetic concurrent ordinary edit.\n")
            return result

        with patch.object(runner, "run_check", side_effect=completed_with_ordinary_drift):
            self.assertEqual(core.execute(self.store, "check")["reason"], "inputs_changed_during_check")
        before = self.store_bytes()
        code, resumed = self.result("resume")
        self.assertEqual((code, resumed["reason"], resumed["next"]),
                         (1, "inputs_changed_during_check", "inspect_then_new_run"))
        self.assertEqual(self.store_bytes(), before)

    def test_timeout_receipt_blocks_close_at_execution_seam(self):
        self.start()
        def timed_out(*_):
            return runner._bounded_process([sys.executable, "-c", "import time; time.sleep(2)"],
                                           self.repo, runner.environment(), timeout=0.1)[0]
        with patch.object(runner, "run_check", side_effect=timed_out):
            result = core.execute(self.store, "check")
        self.assertEqual(result["reason"], "check_timeout")
        self.rejected("close", "check_timeout")
        before = self.store_bytes()
        self.assertEqual(self.result("resume")[1]["next"], "inspect_then_check")
        self.assertEqual(self.store_bytes(), before)

    def test_output_cap_receipt_blocks_close_at_execution_seam(self):
        self.start()
        def too_much_output(*_):
            return runner._bounded_process([sys.executable, "-c", "import os; os.write(1,b'x'*10000)"],
                                           self.repo, runner.environment(), output_limit=1024)[0]
        with patch.object(runner, "run_check", side_effect=too_much_output):
            result = core.execute(self.store, "check")
        self.assertEqual(result["reason"], "check_output_limit")
        self.rejected("close", "check_output_limit")
        before = self.store_bytes()
        self.assertEqual(self.result("resume")[1]["next"], "inspect_then_check")
        self.assertEqual(self.store_bytes(), before)

    def test_failure_preserved_after_new_check(self):
        self.start()
        (self.repo / "sample.txt").write_text("bad  \n")
        self.git("add", "sample.txt")
        self.assertEqual(self.result("check")[0], 1)
        failure = (self.store / "0004.json").read_bytes()
        (self.repo / "sample.txt").write_text("good\n")
        self.git("add", "sample.txt")
        self.assertEqual(self.result("check")[0], 0)
        self.assertEqual(failure, (self.store / "0004.json").read_bytes())

    def test_scoped_attributes_cannot_waive_failed_whitespace_check(self):
        app = self.attributes_fixture()
        sample = app / "sample.txt"
        self.start()
        sample.write_text("bad  \n")
        self.git("add", "app/sample.txt")
        code, failed = self.result("check")
        self.assertEqual((code, failed["reason"]), (1, "check_failed"))
        before = self.store_bytes()
        (app / ".gitattributes").write_text("sample.txt -whitespace\n")
        self.git("add", "app/.gitattributes")
        self.rejected("check", "git_control_changed")
        self.rejected("close", "git_control_changed")
        self.assertEqual(sample.read_text(), "bad  \n")
        self.assertEqual(before, self.store_bytes())

    def test_root_attributes_creation_is_protected_inside_scope(self):
        self.contract_change(scope=["sample.txt", ".gitattributes"])
        self.start()
        (self.repo / ".gitattributes").write_text("sample.txt -whitespace\n")
        self.git("add", ".gitattributes")
        self.rejected("check", "git_control_changed")

    def test_case_variant_attributes_cannot_waive_failed_whitespace_check(self):
        app = self.attributes_fixture()
        self.start()
        (app / "sample.txt").write_text("bad  \n")
        self.git("add", "app/sample.txt")
        code, failed = self.result("check")
        self.assertEqual((code, failed["reason"]), (1, "check_failed"))
        (app / ".GITATTRIBUTES").write_text("sample.txt -whitespace\n")
        if not (app / ".gitattributes").is_file():
            self.skipTest("case-sensitive filesystem: alternate name is not a Git control")
        self.git("add", "app/.GITATTRIBUTES")
        self.rejected("check", "git_control_changed")

    def test_root_attributes_creation_is_protected_outside_scope(self):
        self.start()
        (self.repo / ".gitattributes").write_text("sample.txt -whitespace\n")
        self.rejected("check", "git_control_changed")

    def test_scoped_attributes_worktree_creation_is_protected(self):
        app = self.attributes_fixture()
        self.start()
        (app / ".gitattributes").write_text("sample.txt -whitespace\n")
        self.rejected("check", "git_control_changed")

    def test_scoped_attributes_index_only_creation_is_protected(self):
        app = self.attributes_fixture()
        self.start()
        attributes = app / ".gitattributes"
        attributes.write_text("sample.txt -whitespace\n")
        self.git("add", "app/.gitattributes")
        attributes.unlink()
        self.rejected("check", "git_control_changed")

    def test_scoped_attributes_modification_is_protected(self):
        app = self.attributes_fixture("sample.txt whitespace\n")
        self.start()
        (app / ".gitattributes").write_text("sample.txt -whitespace\n")
        self.git("add", "app/.gitattributes")
        self.rejected("check", "git_control_changed")

    def test_scoped_attributes_worktree_modification_is_protected(self):
        app = self.attributes_fixture("sample.txt whitespace\n")
        self.start()
        (app / ".gitattributes").write_text("sample.txt -whitespace\n")
        self.rejected("check", "git_control_changed")

    def test_scoped_attributes_index_only_modification_is_protected(self):
        baseline_rules = "sample.txt whitespace\n"
        app = self.attributes_fixture(baseline_rules)
        self.start()
        attributes = app / ".gitattributes"
        attributes.write_text("sample.txt -whitespace\n")
        self.git("add", "app/.gitattributes")
        attributes.write_text(baseline_rules)
        self.rejected("check", "git_control_changed")

    def test_scoped_attributes_removal_is_protected(self):
        app = self.attributes_fixture("sample.txt whitespace\n")
        self.start()
        (app / ".gitattributes").unlink()
        self.git("add", "app/.gitattributes")
        self.rejected("check", "git_control_changed")

    def test_scoped_attributes_worktree_removal_is_protected(self):
        app = self.attributes_fixture("sample.txt whitespace\n")
        self.start()
        (app / ".gitattributes").unlink()
        self.rejected("check", "git_control_changed")

    def test_scoped_attributes_index_only_removal_is_protected(self):
        baseline_rules = "sample.txt whitespace\n"
        app = self.attributes_fixture(baseline_rules)
        self.start()
        attributes = app / ".gitattributes"
        attributes.unlink()
        self.git("add", "app/.gitattributes")
        attributes.write_text(baseline_rules)
        self.rejected("check", "git_control_changed")

    def test_normal_scoped_edit_with_frozen_attributes_closes(self):
        app = self.attributes_fixture("sample.txt whitespace\n")
        self.start()
        (app / "sample.txt").write_text("approved change\n")
        self.git("add", "app/sample.txt")
        code, result = self.result("check")
        self.assertEqual((code, result["state"]), (0, "CHECKED"))
        self.assertEqual(self.review(result["snapshot"])[0], 0)
        self.assertEqual(self.result("close")[1]["state"], "CLOSED")

    def test_baseline_attributes_define_the_git_whitespace_check(self):
        app = self.attributes_fixture("sample.txt -whitespace\n")
        self.start()
        (app / "sample.txt").write_text("baseline rules permit this  \n")
        self.git("add", "app/sample.txt")
        code, result = self.result("check")
        self.assertEqual((code, result["state"]), (0, "CHECKED"))

    def test_input_drift_after_check(self):
        self.checked()
        (self.repo / "sample.txt").write_text("changed\n")
        self.rejected("close", "stale_check")

    def test_rule_drift_after_check(self):
        self.checked()
        (self.repo / "AGENTS.md").write_text("changed rules\n")
        self.rejected("close", "source_changed")

    def test_ignored_hidden_drift(self):
        (self.repo / ".gitignore").write_text(".hidden\n")
        (self.repo / ".hidden").write_text("synthetic ignored input\n")
        self.checked()
        (self.repo / ".hidden").write_text("synthetic changed ignored input\n")
        self.rejected("close", "outside_scope_changed")

    def test_index_drift_after_check(self):
        self.checked()
        (self.repo / "sample.txt").write_text("stage-only change\n")
        self.git("add", "sample.txt")
        (self.repo / "sample.txt").write_text("baseline\n")
        self.rejected("close", "stale_check")

    def test_index_outside_scope_change(self):
        (self.repo / "outside.txt").write_text("outside baseline\n")
        self.git("add", "outside.txt")
        self.git("commit", "-m", "fixture outside baseline")
        self.start()
        (self.repo / "outside.txt").write_text("outside modified\n")
        self.git("add", "outside.txt")
        (self.repo / "outside.txt").write_text("outside baseline\n")
        self.rejected("check", "outside_scope_index_changed")

    def test_contract_drift(self):
        self.checked()
        self.contract_change(run_id="altered")
        self.rejected("close", "contract_changed")

    def test_preset_drift_in_memory(self):
        self.checked()
        with patch.object(runner, "CHECK_ARGS", ("status",)):
            with self.assertRaisesRegex(RuntimeError, "execution_or_contract_changed"):
                core.execute(self.store, "close")

    def test_program_and_test_drift(self):
        self.launch_root = self.root / "program"
        shutil.copytree(ROOT / "taskproof", self.launch_root / "taskproof")
        (self.launch_root / "tests").mkdir()
        (self.launch_root / "tests" / "fixture.py").write_text("# synthetic test input\n")
        self.checked()
        (self.launch_root / "taskproof" / "__init__.py").write_text("# program changed\n")
        self.rejected("close", "execution_or_contract_changed")

    def test_tests_are_bound(self):
        self.launch_root = self.root / "program"
        shutil.copytree(ROOT / "taskproof", self.launch_root / "taskproof")
        (self.launch_root / "tests").mkdir()
        (self.launch_root / "tests" / "fixture.py").write_text("# synthetic test input\n")
        self.checked()
        (self.launch_root / "tests" / "fixture.py").write_text("# changed test input\n")
        self.rejected("close", "execution_or_contract_changed")

    def test_evidence_receipt_signature_and_journal_tamper(self):
        self.checked()
        path = self.store / "0004.json"
        original = path.read_bytes()
        for mutate in (lambda value: value["payload"]["journal"].update(stdout_bytes=999),
                       lambda value: value.update(signature="0" * 64),
                       lambda value: value["payload"].update(snapshot_digest="0" * 64)):
            value = json.loads(original)
            mutate(value)
            path.write_text(json.dumps(value))
            self.rejected("close", "evidence_tampered")
            path.write_bytes(original)

    def test_cross_run_replay_with_same_local_owner_key_rejected(self):
        self.checked()
        path = self.store / "0004.json"
        value = json.loads(path.read_text())
        value["run_id"] = "other-run"
        key = (self.store / "key").read_bytes()
        body = {key: item for key, item in value.items() if key != "signature"}
        value["signature"] = hmac.new(key, snapshot.canonical(body), hashlib.sha256).hexdigest()
        path.write_bytes(snapshot.canonical(value))
        self.rejected("close", "store_binding_mismatch")

    def test_truncated_evidence(self):
        self.checked()
        (self.store / "0004.json").write_bytes(b'{"schema":')
        self.rejected("close", "invalid_json")

    def test_unknown_fields_and_shell_contract_rejected(self):
        self.contract_change(shell="echo SYNTHETIC_INJECTION")
        self.rejected_contract("unknown_or_missing_fields")

    def test_duplicate_json_fields_rejected(self):
        self.contract.write_text('{"schema":1,"schema":1}')
        self.rejected_contract("duplicate_fields")

    def test_schema_bool_rejected(self):
        self.contract_change(schema=True)
        self.rejected_contract("unsupported_schema")

    def rejected_contract(self, reason):
        code, result = self.result("contract", str(self.contract))
        self.assertEqual((code, result["reason"]), (1, reason), result)
        self.assertFalse(self.store.exists())

    def test_traversal_rejected_before_store_creation(self):
        self.contract_change(scope=["../escape"])
        self.rejected_contract("invalid_relative_path")

    def test_git_directory_case_alias_contract_paths_rejected(self):
        original = json.loads(self.contract.read_text())
        for field in ("scope", "sources"):
            for index, alias in enumerate((".git", ".GIT", ".Git", ".GIT/info/attributes", ".Git/config")):
                with self.subTest(field=field, path=alias):
                    self.store = self.root / (field + "-" + str(index))
                    value = dict(original)
                    value[field] = ["sample.txt", alias] if field == "scope" else [alias]
                    self.contract.write_text(json.dumps(value))
                    code, result = self.result("contract", str(self.contract))
                    self.assertEqual((code, result.get("reason")), (1, "invalid_relative_path"), result)
                    self.assertFalse(self.store.exists())

    def test_git_directory_case_alias_baseline_rejected(self):
        for index, alias in enumerate((".GIT", ".Git")):
            with self.subTest(alias=alias):
                self.store = self.root / ("baseline-alias-" + str(index))
                self.assertEqual(self.result("contract", str(self.contract))[0], 0)
                before = self.store_bytes()
                renamed = self.repo / alias
                (self.repo / ".git").rename(renamed)
                try:
                    self.assertIn(alias, [path.name for path in self.repo.iterdir()])
                    self.rejected("baseline", "unsupported_git_directory_case")
                    self.assertEqual(before, self.store_bytes())
                finally:
                    renamed.rename(self.repo / ".git")

    def test_late_git_directory_case_alias_cannot_waive_failed_check(self):
        self.start()
        (self.repo / "sample.txt").write_text("bad  \n")
        self.git("add", "sample.txt")
        code, failed = self.result("check")
        self.assertEqual((code, failed["reason"]), (1, "check_failed"))
        before = self.store_bytes()
        renamed = self.repo / ".GIT"
        (self.repo / ".git").rename(renamed)
        if not (self.repo / ".git").is_dir():
            self.skipTest("case-sensitive filesystem: renamed directory is not a Git case alias")
        (renamed / "info").mkdir(exist_ok=True)
        (renamed / "info" / "attributes").write_text("sample.txt -whitespace\n")
        self.rejected("check", "unsupported_git_directory_case")
        self.rejected("close", "unsupported_git_directory_case")
        self.assertEqual(before, self.store_bytes())

    def test_late_git_directory_case_alias_blocks_reviewed_close(self):
        identity = self.checked()
        self.assertEqual(self.review(identity)[0], 0)
        before = self.store_bytes()
        (self.repo / ".git").rename(self.repo / ".Git")
        self.rejected("close", "unsupported_git_directory_case")
        self.assertEqual(before, self.store_bytes())

    def test_additional_git_directory_case_alias_rejected(self):
        if (self.repo / ".GIT").exists():
            self.skipTest("case-insensitive filesystem: cannot create distinct reserved case alias")
        self.contract_change(scope=["sample.txt", ".GIT/info/attributes"])
        self.rejected_contract("invalid_relative_path")
        self.contract_change(scope=["sample.txt"])
        self.start()
        (self.repo / ".GIT").mkdir()
        self.rejected("check", "unsupported_git_directory_case")

    def test_symlink_escaping_repository(self):
        (self.repo / "escape").symlink_to(self.contract)
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        self.rejected("baseline", "symlink_unsupported")

    def test_special_entry(self):
        os.mkfifo(self.repo / "fifo")
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        self.rejected("baseline", "special_entry_unsupported")

    def test_directory_symlink_race_does_not_follow_external_file(self):
        directory = self.repo / "nested"
        directory.mkdir()
        external = self.root / "external"
        external.mkdir()
        (external / "external-sensitive-fixture.txt").write_text("SYNTHETIC_EXTERNAL_SECRET\n")
        original_open = os.open
        opened_private = []
        def race_open(path, flags, *args, **kwargs):
            if str(path) == "nested" and kwargs.get("dir_fd") is not None:
                directory.rmdir()
                directory.symlink_to(external, target_is_directory=True)
            if str(path) == "external-sensitive-fixture.txt":
                opened_private.append(path)
            return original_open(path, flags, *args, **kwargs)
        with patch.object(snapshot.os, "open", side_effect=race_open):
            with self.assertRaises(OSError):
                snapshot.inventory(self.repo)
        self.assertEqual(opened_private, [])

    def test_oversize_contract_before_read(self):
        self.contract.write_bytes(b" " * 65537)
        self.rejected_contract("file_limit")

    def test_oversize_inventory_file(self):
        with (self.repo / "large").open("wb") as stream:
            stream.truncate(snapshot.FILE_LIMIT + 1)
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        self.rejected("baseline", "file_limit")

    def test_incomplete_snapshot_count_budget(self):
        with patch.object(snapshot, "COUNT_LIMIT", 2):
            with self.assertRaisesRegex(RuntimeError, "snapshot_limit"):
                snapshot.capture(self.repo, self.contract)

    def test_git_include_unsupported(self):
        self.git("config", "include.path", str(self.root / "external-config"))
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        self.rejected("baseline", "unsupported_git_config")

    def test_external_git_objects_unsupported(self):
        (self.repo / ".git" / "objects" / "info" / "alternates").write_text(str(self.root) + "\n")
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        self.rejected("baseline", "unsupported_git_control")

    def test_no_existing_head_rejected(self):
        (self.repo / ".git" / "refs" / "heads" / "master").unlink()
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        self.rejected("baseline", "git_snapshot_incomplete")

    def test_active_hooks_unsupported(self):
        (self.repo / ".git" / "hooks").mkdir()
        (self.repo / ".git" / "hooks" / "pre-commit").write_text("synthetic non-executable data\n")
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        self.rejected("baseline", "active_hooks_unsupported")

    def test_index_skip_flag_unsupported(self):
        self.git("update-index", "--skip-worktree", "sample.txt")
        self.assertEqual(self.result("contract", str(self.contract))[0], 0)
        self.rejected("baseline", "unsupported_index_flags")

    def test_review_mode_forbids_drift(self):
        self.contract_change(mode="Review")
        self.start()
        (self.repo / "sample.txt").write_text("changed\n")
        self.rejected("check", "review_mode_drift")

    def test_investigation_reports_only_the_supported_claim(self):
        self.contract_change(mode="Investigation")
        self.assertEqual(self.review()[0], 0)
        self.assertEqual(self.result("close")[1]["claim"], "staged-whitespace")

    def test_product_tdd_requires_real_red_and_is_explicitly_unsupported(self):
        self.contract_change(mode="TDD-first")
        self.rejected_contract("tdd_first_unsupported")

    def test_required_independence_unknown(self):
        self.contract_change(review={"required": True, "independence": "required"})
        identity = self.checked()
        code, result = self.review(identity)
        self.assertEqual((code, result["reason"]), (1, "independence_unknown"))
        self.rejected("close", "independence_unknown")
        resumed = self.result("resume")[1]
        self.assertEqual((resumed["reason"], resumed["next"]), ("independence_unknown", "trust_adapter_required"))

    def test_unknown_review_fields_cannot_claim_independence(self):
        identity = self.checked()
        code, result = self.review(identity, independence="proved")
        self.assertEqual((code, result["reason"]), (1, "unknown_or_missing_fields"))

    def test_wrong_snapshot_operator_review_rejected(self):
        self.checked()
        code, result = self.review("0" * 64)
        self.assertEqual((code, result["reason"]), (1, "invalid_operator_review"))

    def test_review_input_cannot_be_inside_repository_or_store(self):
        self.checked()
        code, result = self.result("review", str(self.repo / "AGENTS.md"))
        self.assertEqual((code, result["reason"]), (1, "review_input_must_be_outside_repository"))
        code, result = self.result("review", str(self.store / "0001.json"))
        self.assertEqual((code, result["reason"]), (1, "review_input_must_be_outside_store"))

    def test_interrupted_running_resume_has_no_execution_or_writes(self):
        self.start()
        with patch.object(runner, "run_check", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                core.execute(self.store, "check")
        before = self.store_bytes()
        with patch.object(runner, "run_check", side_effect=AssertionError("must not execute")) as mocked:
            result = core.execute(self.store, "resume")
        self.assertEqual((result["state"], result["last_confirmed"], result["reason"]),
                         ("BLOCKED", "BASELINED", "interrupted_check"))
        self.assertEqual(result["next"], "inspect_then_new_run")
        self.assertEqual(mocked.call_count, 0)
        self.assertEqual(before, self.store_bytes())
        self.rejected("check", "interrupted_check")

    def test_store_inside_repository_rejected(self):
        self.store = self.repo / ".evidence"
        self.rejected_contract("store_must_be_outside_repository")

    def test_concurrent_store_writer_rejected(self):
        self.start()
        with Store(self.store).locked():
            self.rejected("check", "store_busy")

    def test_store_permissions(self):
        self.start()
        self.assertEqual(self.store.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.store / "key").stat().st_mode & 0o777, 0o600)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in self.store.iterdir()))


class RunnerTests(unittest.TestCase):
    def run_seam(self, code, **limits):
        return runner._bounded_process([sys.executable, "-c", code], ROOT,
                                       runner.environment(), **limits)[0]

    def test_timeout_kills_process(self):
        result = self.run_seam("import time; time.sleep(2)", timeout=0.1)
        self.assertTrue(result["timeout"])
        self.assertNotEqual(result["exit_code"], 0)

    def test_output_cap_discards_synthetic_secret(self):
        result = self.run_seam("import os; os.write(1,b'SYNTHETIC_SECRET'*10000)", output_limit=1024)
        self.assertTrue(result["truncated"])
        self.assertGreater(result["stdout_bytes"], 1024)
        self.assertNotIn("SYNTHETIC", json.dumps(result))

    def test_supported_resource_limits_are_applied(self):
        code = ("import resource; expected={'RLIMIT_CPU':5,'RLIMIT_FSIZE':0,"
                "'RLIMIT_NOFILE':64,'RLIMIT_CORE':0}; "
                "assert all(resource.getrlimit(getattr(resource,k))==(v,v) for k,v in expected.items())")
        result = self.run_seam(code)
        self.assertEqual(result["exit_code"], 0)

    def test_environment_does_not_inherit_secrets_or_instructions(self):
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/synthetic/evil", "SYNTHETIC_SECRET": "private"}):
            env = runner.environment()
        self.assertNotIn("SYNTHETIC_SECRET", env)
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)

    def test_no_user_shell_argv_preset(self):
        self.assertEqual(runner.CHECK_ARGS, ("--no-pager", "diff", "--cached", "--check", "--no-ext-diff",
                                             "--no-textconv", "HEAD", "--"))


if __name__ == "__main__":
    unittest.main()
