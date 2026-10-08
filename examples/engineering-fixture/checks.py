"""Trusted fixture launcher; argv is supplied only by external preset configuration."""
from pathlib import Path
import subprocess
import sys

mode, compiler, sdk = sys.argv[1:]
Path(".build").mkdir(exist_ok=True)
if mode == "tests":
    args = [compiler, "src/foo.c", "tests/regression.c", "-o", ".build/regression"]
elif mode == "typecheck":
    args = [compiler, "-Werror", "-fsyntax-only", "src/foo.c"]
elif mode == "build":
    args = [compiler, "-c", "src/foo.c", "-o", ".build/foo.o"]
else:
    raise SystemExit(2)
if sdk:
    args[1:1] = ["-isysroot", sdk]
result = subprocess.run(args).returncode
if result == 0 and mode == "tests":
    result = subprocess.run([str(Path(".build/regression").absolute())]).returncode
raise SystemExit(result)
