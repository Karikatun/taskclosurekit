"""Regressions for independently confirmed v2.1 dispatch/lifecycle/persistence defects."""
import json
import os
import signal
from pathlib import Path
from dataclasses import replace
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from taskclosurekit import application
from taskclosurekit.execution.presets import Preset, parse_preset, validate_dispatch
from taskclosurekit.execution.runner import bounded_process
import test_v21_engineering
from test_v2_cycle import ROOT

class RemediationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_v21_engineering.EngineeringTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def selected_tests(self, argv, authority=()):
        x = self.fixture
        x.presets[0]["argv"] = argv
        x.presets[0]["authority_inputs"] = list(authority)
        x.config_value["presets"] = [x.presets[0]]
        x.value["authority"]["execution"]["presets"] = ["project-tests"]
        x.value["acceptance"] = [x.value["acceptance"][0]]
        x.value["authority"]["write"] = [item for item in x.value["authority"]["write"] if item != "checks.py"] + ["checks.py"]
        x.input.write_text(json.dumps(x.value)); x.config.write_text(json.dumps(x.config_value))
        return x

    def test_option_operands_cannot_hide_mutable_python_launcher(self):
        x = self.selected_tests(["-B", "-W", "ignore", "checks.py", "tests"])
        with self.assertRaisesRegex(RuntimeError, "undeclared_command_authority"):
            application.create(str(x.store), str(x.input), preset_config=str(x.config))
        self.assertFalse(x.store.exists())

    def test_missing_python_launcher_cannot_be_created_after_authorization(self):
        x = self.selected_tests(["-B", "checks.py", "tests"])
        (x.repo / "checks.py").unlink()
        with self.assertRaises(RuntimeError):
            application.create(str(x.store), str(x.input), preset_config=str(x.config))
        self.assertFalse(x.store.exists())

    def test_successful_parent_cleans_same_group_background_child_before_return(self):
        x = self.fixture
        marker = x.root / "late-marker"
        ready = x.root / "child-ready"
        child = "import time;from pathlib import Path;Path(" + repr(str(ready)) + ").write_text('ready');time.sleep(2.3);Path(" + repr(str(marker)) + ").write_text('late')"
        handshake = "while not Path(" + repr(str(ready)) + ").exists():\n time.sleep(0.01)"
        parent = "import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,'-c'," + repr(child) + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);exec(" + repr(handshake) + ")"
        preset = Preset("background", str(Path(sys.executable).resolve()), ("-c", parent),
                        "contract-repository", (("PATH", "/usr/bin:/bin"),), 2, 65536, ("late-marker", "child-ready"))
        journal = bounded_process(preset, str(x.root))
        self.assertEqual(journal["exit_code"], 0)
        self.assertFalse(journal["timeout"], journal)
        self.assertTrue(ready.exists(), "child did not reach its ready handshake")
        time.sleep(2.5)
        self.assertFalse(marker.exists(), "same-group child survived bounded runner return")

    def test_interruption_survives_cleanup_after_owned_group_is_stopped(self):
        x = self.fixture
        marker = x.root / "interrupted-late-marker"
        ready = x.root / "interrupted-child-ready"
        child = "import time;from pathlib import Path;Path(" + repr(str(ready)) + ").write_text('ready');time.sleep(0.5);Path(" + repr(str(marker)) + ").write_text('late')"
        handshake = "while not Path(" + repr(str(ready)) + ").exists():\n time.sleep(0.01)"
        parent = "import os,signal,subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,'-c'," + repr(child) + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);exec(" + repr(handshake) + ");os.kill(os.getppid(),signal.SIGINT);time.sleep(5)"
        preset = Preset("interrupted", str(Path(sys.executable).resolve()), ("-c", parent),
                        "contract-repository", (("PATH", "/usr/bin:/bin"),), 2, 65536,
                        (marker.name, ready.name))
        real_killpg = os.killpg
        stopped_groups = set()

        def stop_owned_group(pgid, signum):
            # Reproduce an OS refusal after successful cleanup of a real group.
            if pgid in stopped_groups:
                raise PermissionError("owned process group is already stopped")
            real_killpg(pgid, signum)
            stopped_groups.add(pgid)

        previous_handler = signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            with patch("os.killpg", side_effect=stop_owned_group):
                with self.assertRaises(KeyboardInterrupt):
                    bounded_process(preset, str(x.root))
        finally:
            signal.signal(signal.SIGINT, previous_handler)
        self.assertTrue(ready.exists(), "child did not reach its ready handshake")
        time.sleep(0.6)
        self.assertFalse(marker.exists(), "same-group child survived interruption")

    def test_first_cleanup_permission_failure_is_reported_after_retry_stops_group(self):
        x = self.fixture
        marker = x.root / "permission-failure-late-marker"
        script = "import time;from pathlib import Path;time.sleep(0.5);Path(" + repr(str(marker)) + ").write_text('late')"
        preset = Preset("cleanup-denied", str(Path(sys.executable).resolve()), ("-c", script),
                        "contract-repository", (("PATH", "/usr/bin:/bin"),), 0.1, 65536,
                        (marker.name,))
        real_killpg = os.killpg
        denied = []

        def deny_first_cleanup(pgid, signum):
            if not denied:
                denied.append(pgid)
                raise PermissionError("first cleanup denied")
            real_killpg(pgid, signum)

        with patch("os.killpg", side_effect=deny_first_cleanup):
            with self.assertRaisesRegex(PermissionError, "first cleanup denied"):
                bounded_process(preset, str(x.root))
        time.sleep(0.6)
        self.assertFalse(marker.exists(), "owned child survived cleanup retry")

    def test_unused_authorized_check_is_rejected_without_journal_mutation_and_resumes(self):
        x = self.fixture
        x.value["acceptance"] = x.value["acceptance"][:2]
        x.input.write_text(json.dumps(x.value))
        x.prepare(); x.fix()
        before = {p.name: p.read_bytes() for p in x.store.iterdir()}
        with self.assertRaisesRegex(RuntimeError, "preset_has_no_criterion"):
            x.check("build")
        self.assertEqual(before, {p.name: p.read_bytes() for p in x.store.iterdir()})
        command = [sys.executable, "-m", "taskclosurekit", "--store", str(x.store), "status", "--json"]
        result = subprocess.run(command, cwd=ROOT, env=x.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(json.loads(result.stdout)["operational"]["status"], "ok")
        self.assertEqual(before, {p.name: p.read_bytes() for p in x.store.iterdir()})

    def test_python_complete_supported_option_forms_find_same_immutable_launcher(self):
        x = self.fixture
        entries = {"checks.py": {"kind": "file"}}
        base = parse_preset(x.presets[0])
        variants = [("-B", "-W", "ignore", "checks.py", "tests"),
                    ("-B", "-Wignore", "checks.py", "tests"),
                    ("-B", "-X", "dev", "checks.py", "tests"),
                    ("-BXdev", "checks.py", "tests"),
                    ("--check-hash-based-pycs", "always", "checks.py", "tests"),
                    ("--", "checks.py", "tests"),
                    ("-B", str(x.repo / "checks.py"), "tests")]
        for argv in variants:
            with self.subTest(argv=argv):
                bound = replace(base, argv=argv)
                validate_dispatch(bound, entries, str(x.repo))
                with self.assertRaisesRegex(RuntimeError, "undeclared_command_authority"):
                    validate_dispatch(replace(bound, authority_inputs=()), entries, str(x.repo))
        for argv in [("-Z", "checks.py"), ("-W",), ("--unknown", "checks.py"), ("-X",), ("--",)]:
            with self.subTest(invalid=argv), self.assertRaises(RuntimeError):
                validate_dispatch(replace(base, argv=argv), entries, str(x.repo))

    def test_module_package_and_missing_targets_are_explicitly_bound(self):
        x = self.fixture
        base = parse_preset(x.presets[0])
        for argv in [("-m", "projectcheck"), ("-mprojectcheck",)]:
            module = replace(base, argv=argv, authority_inputs=("projectcheck.py",))
            validate_dispatch(module, {"projectcheck.py": {"kind": "file"}})
            with self.assertRaisesRegex(RuntimeError, "preset_launch_target_required"):
                validate_dispatch(module, {})
        package = replace(base, argv=("-m", "checks"), authority_inputs=("checks/__init__.py", "checks/__main__.py"))
        entries = {item: {"kind": "file"} for item in package.authority_inputs}
        validate_dispatch(package, entries)
        for missing in package.authority_inputs:
            with self.subTest(missing=missing), self.assertRaises(RuntimeError):
                validate_dispatch(package, {item: value for item, value in entries.items() if item != missing})
        with self.assertRaises(RuntimeError):
            validate_dispatch(replace(package, argv=("-m", "unittest")), {})
        validate_dispatch(replace(base, argv=("-c", "pass"), authority_inputs=()), {})

    def test_node_and_bun_loaders_cannot_hide_in_test_mode_or_missing_files(self):
        x = self.fixture
        base = parse_preset(x.presets[0])
        entries = {"checks.py": {"kind": "file"}}
        variants = [("node", ("--require", "checks.py", "--test", "tests")),
                    ("node", ("--test", "tests", "--require=checks.py")),
                    ("node", ("-rchecks.py", "--test", "tests")),
                    ("node", ("-e", "pass", "--require", "./checks.py")),
                    ("bun", ("--cwd", "tests", "test", "--preload", "checks.py"))]
        for executable, argv in variants:
            with self.subTest(executable=executable, argv=argv):
                preset = replace(base, executable="/absolute/" + executable, argv=argv, authority_inputs=())
                with self.assertRaises(RuntimeError):
                    validate_dispatch(preset, entries, str(x.repo))
        validate_dispatch(replace(base, executable="/absolute/node", argv=("--test", "tests", "--require=./checks.py")), entries, str(x.repo))
        validate_dispatch(replace(base, executable="/absolute/node", argv=("-e", "pass", "--require", str(x.repo / "checks.py"))), entries, str(x.repo))
        for executable in ("node", "bun", "sh", "env"):
            with self.subTest(executable=executable), self.assertRaises(RuntimeError):
                validate_dispatch(replace(base, executable="/absolute/" + executable, argv=("--unknown", "checks.py")), entries)

    def test_missing_package_cannot_shadow_bound_python_module_from_write_scope(self):
        base = parse_preset(self.fixture.presets[0])
        module = replace(base, argv=("-m", "projectcheck"), authority_inputs=("projectcheck.py",))
        with self.assertRaisesRegex(RuntimeError, "preset_launch_target_required"):
            validate_dispatch(module, {"projectcheck.py": {"kind": "file"}}, write_scope=("projectcheck",))

    def test_python_isolated_module_resolution_is_not_misbound_to_local_file(self):
        base = parse_preset(self.fixture.presets[0])
        for flag in ("-I", "-P", "-BI"):
            with self.subTest(flag=flag), self.assertRaisesRegex(RuntimeError, "unsupported_dispatch_arguments"):
                validate_dispatch(replace(base, argv=(flag, "-m", "projectcheck"),
                                          authority_inputs=("projectcheck.py",)),
                                  {"projectcheck.py": {"kind": "file"}})

    def test_named_package_dispatch_requires_present_script_and_unambiguous_tail(self):
        x = self.fixture
        manifest = x.repo / "package.json"
        manifest.write_text('{"scripts":{"checks":"fixed approved command"}}')
        base = replace(parse_preset(x.presets[0]), authority_inputs=("package.json",))
        entries = {"package.json": {"kind": "file"}}
        for executable in ("bun", "npm", "pnpm", "yarn"):
            preset = replace(base, executable="/absolute/" + executable)
            with self.subTest(executable=executable, boundary="positive"):
                validate_dispatch(replace(preset, argv=("run", "checks")), entries, str(x.repo))
                validate_dispatch(replace(preset, argv=("run", "checks", "--", "--user-arg")), entries, str(x.repo))
            with self.subTest(executable=executable, boundary="tail"):
                with self.assertRaisesRegex(RuntimeError, "unsupported_dispatch_arguments"):
                    validate_dispatch(replace(preset, argv=("run", "checks", "--prefix", "future")), entries, str(x.repo))
            with self.subTest(executable=executable, boundary="missing"):
                with self.assertRaisesRegex(RuntimeError, "preset_launch_target_required"):
                    validate_dispatch(replace(preset, argv=("run", "future")), entries, str(x.repo))
            if executable != "bun":
                with self.subTest(executable=executable, boundary="cwd"):
                    with self.assertRaisesRegex(RuntimeError, "unsupported_dispatch_arguments"):
                        validate_dispatch(replace(preset, argv=("--cwd", "future", "run", "checks")), entries, str(x.repo))

    def test_timeout_and_output_limit_also_clean_background_group(self):
        x = self.fixture
        for mode in ("timeout", "output"):
            with self.subTest(mode=mode):
                marker = x.root / ("late-" + mode)
                ready = x.root / ("ready-" + mode)
                child = "import time;from pathlib import Path;Path(" + repr(str(ready)) + ").write_text('ready');time.sleep(2.3);Path(" + repr(str(marker)) + ").write_text('late')"
                handshake = "while not Path(" + repr(str(ready)) + ").exists():\n time.sleep(0.01)"
                action = "import time;time.sleep(2)" if mode == "timeout" else "print('x' * 4096,flush=True)"
                parent = "import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,'-c'," + repr(child) + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);" + ("exec(" + repr(handshake) + ");" if mode == "output" else "") + action
                preset = Preset("background", str(Path(sys.executable).resolve()), ("-c", parent),
                                "contract-repository", (("PATH", "/usr/bin:/bin"),), 0.1 if mode == "timeout" else 2, 128, (marker.name, ready.name))
                journal = bounded_process(preset, str(x.root))
                self.assertTrue(journal["timeout"] if mode == "timeout" else journal["truncated"], journal)
                if mode == "output":
                    self.assertTrue(ready.exists(), "child did not reach its ready handshake")
                time.sleep(2.5)
                self.assertFalse(marker.exists())
