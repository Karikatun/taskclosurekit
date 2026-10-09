"""Public npm payload, operator and interruption boundaries; no downloaded dependencies."""
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import test_v2_cycle as cycle_fixture

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
NPM = shutil.which("npm")
PYTHON = str(Path(sys.executable).resolve())


def npm_environment(root):
    for filename in ("user.npmrc", "global.npmrc"):
        (root / filename).write_text("")
    return {"PATH": str(Path(NODE).parent) + ":/usr/bin:/bin", "TMPDIR": str(root),
            "npm_config_cache": str(root / "cache"), "npm_config_userconfig": str(root / "user.npmrc"),
            "npm_config_globalconfig": str(root / "global.npmrc"), "npm_config_prefix": str(root / "prefix"),
            "npm_config_audit": "false", "npm_config_fund": "false", "npm_config_offline": "true",
            "npm_config_ignore_scripts": "true", "TASKCLOSUREKIT_PYTHON": PYTHON}


class NpmLauncherTests(unittest.TestCase):
    git = cycle_fixture.V2CycleTests.git

    @classmethod
    def setUpClass(cls):
        cls.package_temp = tempfile.TemporaryDirectory(prefix="taskclosurekit-npm-")
        cls.addClassCleanup(cls.package_temp.cleanup)
        cls.package_root = Path(cls.package_temp.name).resolve()
        cls.npm_env = npm_environment(cls.package_root)
        packed = subprocess.run([NPM, "pack", "--offline", "--ignore-scripts", "--json",
                                 "--pack-destination", str(cls.package_root)], cwd=ROOT,
                                env=cls.npm_env, capture_output=True, text=True, timeout=30)
        if packed.returncode != 0:
            raise AssertionError(packed.stdout + packed.stderr)
        cls.archive = cls.package_root / json.loads(packed.stdout)[0]["filename"]
        cls.payload = cls.package_root / "extracted"
        cls.payload.mkdir()
        with tarfile.open(cls.archive) as archive:
            for member in archive.getmembers():
                if (not member.isfile() or not member.name.startswith("package/") or
                        ".." in Path(member.name).parts):
                    raise AssertionError("unsupported package entry")
                target = cls.payload / member.name.removeprefix("package/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
                target.chmod(member.mode)

    def setUp(self):
        cycle_fixture.V2CycleTests.setUp(self)
        self.launch = self.root / "installed package тест"
        shutil.copytree(self.payload, self.launch)
        self.cwd = self.root / "caller project"
        self.cwd.mkdir()
        self.env["TASKCLOSUREKIT_PYTHON"] = PYTHON

    def command(self, *args):
        return [NODE, str(self.launch / "taskclosurekit/launcher.cjs"), *args]

    def cli(self, *args, human=False, prefix=None):
        command = [*(prefix or self.command()), "--store", str(self.store), *args, "--json"]
        if not human:
            return subprocess.run(command, cwd=self.cwd, env=self.env, capture_output=True,
                                  text=True, timeout=20)
        return self.controlling_terminal(command)

    def controlling_terminal(self, command, interrupt=False):
        import pty
        pid, master = pty.fork()
        if pid == 0:
            os.chdir(self.cwd)
            os.execve(command[0], command, self.env)
        output = bytearray()
        answered = False
        status = None
        deadline = time.monotonic() + 20
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        data = os.read(master, 8192)
                    except OSError as error:
                        if error.errno != 5:
                            raise
                        break
                    if not data:
                        break
                    output.extend(data)
                    match = re.search(rb"typing exactly: ([0-9a-f]{64})", output)
                    if match and not answered:
                        os.write(master, bytes([3]) if interrupt else match.group(1) + b"\n")
                        answered = True
                observed, status = os.waitpid(pid, os.WNOHANG)
                if observed:
                    pid = None
                    # Drain terminal output already buffered before reporting.
                    while select.select([master], [], [], 0)[0]:
                        try:
                            output.extend(os.read(master, 8192))
                        except OSError:
                            break
                    break
            self.assertTrue(answered, output.decode(errors="replace"))
            if pid is not None:
                observed, status = os.waitpid(pid, os.WNOHANG)
                self.assertTrue(observed, "terminal command did not finish")
                pid = None
            text = output.decode(errors="replace")
            lines = [line for line in text.splitlines() if line.startswith("{")]
            return subprocess.CompletedProcess(command, os.waitstatus_to_exitcode(status),
                                               "\n".join(lines), text)
        finally:
            if pid is not None:
                os.kill(pid, signal.SIGTERM)
                os.waitpid(pid, 0)
            os.close(master)

    def successful(self, *args, human=False, prefix=None):
        result = self.cli(*args, human=human, prefix=prefix)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def store_bytes(self):
        return {p.name: p.read_bytes() for p in self.store.iterdir()}

    def prepare(self):
        self.successful("task", "create", str(self.input))
        self.successful("authorize", human=True)
        self.successful("baseline")

    def test_public_launcher_help_from_arbitrary_directory(self):
        result = subprocess.run(self.command("--help"), cwd=self.cwd, env=self.env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("taskclosurekit", result.stdout)

    def test_packaged_payload_has_bin_both_identity_roots_and_no_hooks(self):
        with tarfile.open(self.archive) as payload:
            names = payload.getnames()
            for expected in ("package/taskclosurekit/launcher.cjs", "package/taskclosurekit/_npm_bootstrap.py",
                             "package/tests/test_v2_cycle.py", "package/taskclosurekit/cli/main.py"):
                self.assertIn(expected, names)
            self.assertTrue(payload.getmember("package/taskclosurekit/launcher.cjs").mode & 0o111)
            for root in ("taskclosurekit", "tests"):
                expected = {"package/" + p.relative_to(ROOT).as_posix() for p in (ROOT / root).rglob("*.py")}
                self.assertTrue(expected.issubset(set(names)))
            self.assertFalse(any(any(part in (".scratch", ".git", "__pycache__", "node_modules", "store")
                                     for part in Path(name).parts) or name.endswith((".pyc", ".env")) for name in names))
            manifest = json.load(payload.extractfile("package/package.json"))
            self.assertEqual(manifest["bin"], {"taskclosurekit": "taskclosurekit/launcher.cjs"})
            self.assertEqual(manifest["version"], "2.1.1")
            self.assertEqual(manifest["license"], "UNLICENSED")
            self.assertFalse(manifest.get("scripts"))
            for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                self.assertFalse(manifest.get(key))

    def test_local_npm_exec_and_npx_need_no_clone_or_registry(self):
        env = npm_environment(self.root)
        for executable, arguments in ((NPM, ["exec"]),
                                      (str(Path(NPM).with_name("npx")), [])):
            result = subprocess.run([executable, *arguments, "--offline", "--yes", "--ignore-scripts",
                                     "--package=" + str(self.archive), "--", "taskclosurekit", "--help"],
                                    cwd=self.cwd, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("taskclosurekit", result.stdout)

    def test_cwd_pythonpath_and_npm_path_cannot_replace_bundled_code(self):
        local = self.cwd / "taskclosurekit"
        local.mkdir()
        (local / "__init__.py").write_text("raise RuntimeError('LOCAL_MODULE_EXECUTED')\n")
        malicious = self.cwd / "node_modules/.bin"
        malicious.mkdir(parents=True)
        fake = malicious / "python3"
        fake.write_text("#!/bin/sh\necho PATH_INTERPRETER_EXECUTED\nexit 99\n")
        fake.chmod(0o755)
        (self.cwd / "sitecustomize.py").write_text("raise RuntimeError('SITE_EXECUTED')\n")
        self.env["PYTHONPATH"] = str(self.cwd)
        self.env["PATH"] = str(malicious) + ":/usr/bin:/bin"
        del self.env["TASKCLOSUREKIT_PYTHON"]
        result = subprocess.run(self.command("--help"), cwd=self.cwd, env=self.env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("EXECUTED", result.stdout + result.stderr)
        self.assertFalse(list(self.launch.rglob("*.pyc")))

    def test_python_override_errors_before_store_creation(self):
        for value in ("python3", "", "/nonexistent/taskclosurekit-python", str(self.cwd)):
            with self.subTest(value=value):
                self.env["TASKCLOSUREKIT_PYTHON"] = value
                result = self.cli("task", "create", str(self.input))
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["reasons"], ["invalid_python_override"])
                self.assertFalse(self.store.exists())

    def test_python_spawn_failure_is_environment_error_without_store_writes(self):
        executable = self.root / "broken interpreter"
        executable.write_text("#!/nonexistent/taskclosurekit-interpreter\n")
        executable.chmod(0o755)
        self.env["TASKCLOSUREKIT_PYTHON"] = str(executable)
        result = self.cli("task", "create", str(self.input))
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["reasons"], ["python_launch_failed"])
        self.assertFalse(self.store.exists())

    def test_real_packaged_full_flow_preserves_confirmation_and_exit_codes(self):
        self.store = self.root / "store with spaces хранилище"
        self.input = self.input.rename(self.root / "contract with spaces контракт.json")
        self.successful("task", "create", str(self.input))
        self.assertEqual(self.cli("authorize").returncode, 1)
        self.successful("authorize", human=True)
        self.successful("baseline")
        target = self.repo / "src/foo.py"
        target.write_text("value = 2  \n")
        self.git("add", "src/foo.py")
        self.assertEqual(self.cli("check", "git-index-whitespace-v1").returncode, 1)
        target.write_text("value = 2\n")
        self.git("add", "src/foo.py")
        self.successful("check", "git-index-whitespace-v1")
        self.assertEqual(self.cli("review", "--human").returncode, 1)
        self.successful("review", "--human", human=True)
        self.assertEqual(self.successful("evaluate")["decision"], "CLAIMABLE")
        self.assertEqual(self.cli("close").returncode, 1)
        self.assertEqual(self.successful("close", human=True)["state"], "CLOSED")
        before = self.store_bytes()
        self.assertEqual(self.successful("status", "--next")["state"], "CLOSED")
        self.assertEqual(before, self.store_bytes())
        self.assertEqual(self.cli("unknown-command").returncode, 2)
        target.write_text("value = 3\n")
        self.assertEqual(self.cli("resume").returncode, 4)
        self.env["TASKCLOSUREKIT_PYTHON"] = "relative"
        self.assertEqual(self.cli("status").returncode, 3)

    def test_same_payload_relocation_keeps_evidence_drift_never_rewrites_store(self):
        self.prepare()
        self.successful("check", "git-index-whitespace-v1")
        self.successful("review", "--human", human=True)
        original = self.successful("evaluate")
        before = self.store_bytes()
        relocated = self.root / "another cache entry"
        shutil.copytree(self.launch, relocated)
        self.launch = relocated
        current = self.successful("evaluate")
        self.assertEqual(current["decision"], "CLAIMABLE")
        self.assertEqual(current["snapshot"], original["snapshot"])
        for relative in ("taskclosurekit/launcher.cjs", "taskclosurekit/_npm_bootstrap.py",
                         "taskclosurekit/_primitives/policy.py", "tests/test_v2_cycle.py"):
            with self.subTest(relative=relative):
                target = self.launch / relative
                content = target.read_bytes()
                target.write_bytes(content + b"\n")
                result = self.cli("evaluate")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("authority_changed", json.loads(result.stdout)["reasons"])
                self.assertEqual(before, self.store_bytes())
                target.write_bytes(content)
        self.assertEqual(before, self.store_bytes())

    def test_pack_allowlist_excludes_injected_store_env_cache_and_instructions(self):
        source = self.root / "package candidate"
        shutil.copytree(self.payload, source)
        sentinels = [".scratch/note.md", "AGENTS.md", ".env", "store/key", "taskclosurekit/private.env",
                     "taskclosurekit/__pycache__/cached.pyc", "tests/store/0001.json",
                     "examples/engineering-fixture/store/key", "examples/engineering-fixture/private.env"]
        for relative in sentinels:
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("SYNTHETIC_EXCLUDED_SENTINEL\n")
        destination = self.root / "packed"
        destination.mkdir()
        result = subprocess.run([NPM, "pack", "--offline", "--ignore-scripts", "--json",
                                 "--pack-destination", str(destination)], cwd=source,
                                env=npm_environment(self.root), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with tarfile.open(destination / json.loads(result.stdout)[0]["filename"]) as archive:
            names = set(archive.getnames())
            for relative in sentinels:
                self.assertNotIn("package/" + relative, names)

    def test_node_platform_and_version_preflight_fail_before_mutation(self):
        for setup, reason in [("Object.defineProperty(process,'platform',{value:'win32'})", "unsupported_platform"),
                              ("Object.defineProperty(process.versions,'node',{value:'20.0.0'})", "unsupported_node_version")]:
            with self.subTest(reason=reason):
                launcher = str(self.launch / "taskclosurekit/launcher.cjs")
                code = setup + ";process.argv=['node'," + json.dumps(launcher) + ",'--json'];require(" + json.dumps(launcher) + ")"
                result = subprocess.run([NODE, "-e", code], cwd=self.cwd, env=self.env,
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["reasons"], [reason])
                self.assertFalse(self.store.exists())

    def test_runtime_preflight_rejects_unsupported_environment_before_mutation(self):
        bootstrap = str(self.launch / "taskclosurekit/_npm_bootstrap.py")
        cases = [("import sys;sys.version_info=(3,8,0)", "unsupported_python_version"),
                 ("import sys;sys.platform='win32'", "unsupported_platform"),
                 ("import sys;sys.modules['resource']=None", "unsupported_resource_limits"),
                 ("import shutil;shutil.which=lambda *a,**k:None", "git_unavailable")]
        for setup, reason in cases:
            with self.subTest(reason=reason):
                code = setup + ";import runpy,sys;sys.argv=[" + repr(bootstrap) + ", '--json'];runpy.run_path(" + repr(bootstrap) + ",run_name='__main__')"
                result = subprocess.run([PYTHON, "-I", "-S", "-B", "-c", code], cwd=self.cwd,
                                        env=self.env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["reasons"], [reason])
                self.assertFalse(self.store.exists())

    def test_runtime_binding_change_blocks_without_store_writes(self):
        self.prepare()
        self.successful("check", "git-index-whitespace-v1")
        before = self.store_bytes()
        bootstrap = str(self.launch / "taskclosurekit/_npm_bootstrap.py")
        code = "import sys,runpy;sys.version_info=(3,9,99);sys.argv=[" + repr(bootstrap) + ",*sys.argv[1:]];runpy.run_path(" + repr(bootstrap) + ",run_name='__main__')"
        result = self.cli("evaluate", prefix=[PYTHON, "-I", "-S", "-B", "-c", code])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("authority_changed", json.loads(result.stdout)["reasons"])
        self.assertEqual(before, self.store_bytes())

    def test_sigterm_during_check_cleans_descendants_and_reports_interrupted_state(self):
        self.interrupt_check(signal.SIGTERM, False)

    def test_ctrl_c_process_group_cleans_descendants_and_reports_interrupted_state(self):
        self.interrupt_check(signal.SIGINT, True)

    def interrupt_check(self, signum, group):
        launcher = self.repo / "slow.py"
        launcher.write_text("import subprocess,sys,time\nfrom pathlib import Path\nPath('.out').mkdir(exist_ok=True)\n"
                            "subprocess.Popen([sys.executable,'-B','-c',\"import time;from pathlib import Path;time.sleep(2);Path('.out/late').write_text('late')\"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                            "Path('.out/ready').write_text('ready')\ntime.sleep(3)\n")
        self.git("add", "slow.py")
        self.git("commit", "-m", "slow check fixture")
        self.value["authority"]["read"].append("slow.py")
        self.value["authority"]["write"].append(".out")
        self.value["authority"]["execution"]["presets"] = ["project-slow"]
        self.value["acceptance"][0]["evidence"]["all_of"] = ["project-slow"]
        self.input.write_text(json.dumps(self.value))
        config = self.root / "presets.json"
        config.write_text(json.dumps({"schema": "taskclosurekit/presets/v1", "presets": [
            {"id": "project-slow", "executable": PYTHON, "argv": ["-B", "slow.py"],
             "cwd_rule": "contract-repository", "environment_allowlist": {"PATH": "/usr/bin:/bin"},
             "timeout": 5, "output_limit": 65536, "permitted_writes": [".out"],
             "relevant_inputs": {"source": ["src"], "tests": [], "manifests": [], "lockfiles": [], "config": []},
             "authority_inputs": ["slow.py"], "runtime_inputs": []}]}))
        self.successful("task", "create", str(self.input), "--preset-config", str(config))
        self.successful("authorize", human=True)
        self.successful("baseline")
        process = subprocess.Popen([*self.command(), "--store", str(self.store), "check", "project-slow", "--json"],
                                   cwd=self.cwd, env=self.env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not (self.repo / ".out/ready").exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.repo / ".out/ready").exists(), "slow check did not start")
            if group:
                os.killpg(process.pid, signum)
            else:
                process.send_signal(signum)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 128 + signum, stdout + stderr)
            self.assertIn("interrupted", stderr)
            time.sleep(2.2)
            self.assertFalse((self.repo / ".out/late").exists(), "descendant survived interruption")
            before = self.store_bytes()
            result = self.cli("resume")
            self.assertIn("interrupted_check", json.loads(result.stdout)["reasons"])
            self.assertEqual(before, self.store_bytes())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    def test_terminal_ctrl_c_interrupts_confirmation_without_store_writes(self):
        self.successful("task", "create", str(self.input))
        before = self.store_bytes()
        result = self.controlling_terminal([*self.command(), "--store", str(self.store), "authorize", "--json"],
                                           interrupt=True)
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertIn("interrupted", result.stderr)
        self.assertEqual(before, self.store_bytes())

    def test_targeted_sigint_interrupts_confirmation_without_authority_write(self):
        self.interrupt_confirmation(signal.SIGINT, 130)

    def test_targeted_sigterm_interrupts_confirmation_without_authority_write(self):
        self.interrupt_confirmation(signal.SIGTERM, 143)

    def interrupt_confirmation(self, signum, code):
        self.successful("task", "create", str(self.input))
        import pty
        master, slave = pty.openpty()
        command = [*self.command(), "--store", str(self.store), "authorize", "--json"]
        try:
            process = subprocess.Popen(command, cwd=self.cwd, env=self.env, stdin=slave,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            os.close(slave)
            slave = None
            ready, _, _ = select.select([process.stderr], [], [], 10)
            self.assertTrue(ready)
            self.assertIn("Confirm authorize", process.stderr.readline())
            before = self.store_bytes()
            process.send_signal(signum)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, code, stdout + stderr)
            self.assertIn("interrupted", stderr)
            self.assertEqual(before, self.store_bytes())
        finally:
            if slave is not None:
                os.close(slave)
            os.close(master)


if __name__ == "__main__":
    unittest.main()
