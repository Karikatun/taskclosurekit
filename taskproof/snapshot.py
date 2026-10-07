"""Fail-closed bounded snapshots including hidden, ignored and ordinary Git inputs."""

import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import zlib

from . import runner


FILE_LIMIT = 8 * 1024 * 1024
AGGREGATE_LIMIT = 64 * 1024 * 1024
COUNT_LIMIT = 10000
SNAPSHOT_SECONDS = 10.0
OBJECT_HEADER_LIMIT = 64


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def safe_path(path):
    path = Path(path)
    if not path.is_absolute() or str(path) != os.path.normpath(str(path)):
        raise RuntimeError("unsafe_path")
    # Path.parents is safe for boundary comparisons only after existing spellings
    # match the directory entries. resolve()/casefold() do not establish that on
    # case- or normalization-insensitive filesystems.
    current = Path(path.anchor)
    budget = Budget()
    directory_fd = os.open(str(current), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    observed = []
    try:
        for index, part in enumerate(path.parts[1:], 1):
            budget.check()
            try:
                info = os.stat(part, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                # No subsequent component can already exist below this missing one.
                break
            if stat.S_ISLNK(info.st_mode):
                raise RuntimeError("symlink_unsupported")
            exact = False
            with os.scandir(directory_fd) as scan:
                for item in scan:
                    budget.count += 1
                    budget.check()
                    if item.name == part:
                        exact = True
                        break
            if not exact:
                raise RuntimeError("noncanonical_path")
            current = current / part
            observed.append((current, info.st_dev, info.st_ino, info.st_mode))
            if index < len(path.parts) - 1:
                if not stat.S_ISDIR(info.st_mode):
                    raise RuntimeError("unsafe_path")
                child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=directory_fd)
                opened = os.fstat(child_fd)
                if (opened.st_dev, opened.st_ino, opened.st_mode) != (info.st_dev, info.st_ino, info.st_mode):
                    os.close(child_fd)
                    raise RuntimeError("snapshot_race")
                os.close(directory_fd)
                directory_fd = child_fd
        for current, device, inode, mode in observed:
            now = current.lstat()
            if (now.st_dev, now.st_ino, now.st_mode) != (device, inode, mode):
                raise RuntimeError("snapshot_race")
    finally:
        os.close(directory_fd)
    return path


def path_within(path, boundary):
    """Compare physical existing ancestors, including a future path's nearest parent."""
    path, boundary = safe_path(path), safe_path(boundary)
    try:
        target = boundary.lstat()
    except FileNotFoundError:
        return boundary == path or boundary in path.parents
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino):
            return True
    return False


class Budget:
    def __init__(self):
        self.started = time.monotonic()
        self.count = 0
        self.bytes = 0

    def check(self):
        if (self.count > COUNT_LIMIT or self.bytes > AGGREGATE_LIMIT or
                time.monotonic() - self.started > SNAPSHOT_SECONDS):
            raise RuntimeError("snapshot_limit")


def regular(path, budget, limit=FILE_LIMIT, content=False, dir_fd=None):
    """Read at most limit+1 using a non-following descriptor and identity recheck."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(str(path), flags, dir_fd=dir_fd)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError("special_entry_unsupported")
        if before.st_size > limit:
            raise RuntimeError("file_limit")
        sha = hashlib.sha256()
        retained = bytearray()
        total = 0
        while True:
            budget.check()
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            budget.bytes += len(chunk)
            if total > limit:
                raise RuntimeError("file_limit")
            budget.check()
            sha.update(chunk)
            if content:
                retained.extend(chunk)
        after = os.fstat(fd)
        now = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, key) != getattr(after, key) or
               getattr(after, key) != getattr(now, key) for key in fields):
            raise RuntimeError("snapshot_race")
        return {"kind": "file", "mode": stat.S_IMODE(after.st_mode), "size": total,
                "sha256": sha.hexdigest()}, bytes(retained)
    finally:
        os.close(fd)


def inventory(root, budget=None):
    root = safe_path(root)
    budget = budget or Budget()
    entries = {}

    def visit(directory_fd, prefix):
        before = os.fstat(directory_fd)
        if not stat.S_ISDIR(before.st_mode):
            raise RuntimeError("directory_required")
        with os.scandir(directory_fd) as scan:
            for item in scan:
                budget.count += 1
                budget.check()
                # Paths are never projected into the CLI or execution journal.
                relative = prefix + item.name
                mode = item.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    entries[relative] = {"kind": "directory", "mode": stat.S_IMODE(mode)}
                    child_fd = os.open(item.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=directory_fd)
                    try:
                        visit(child_fd, relative + "/")
                        current = os.stat(item.name, dir_fd=directory_fd, follow_symlinks=False)
                        opened = os.fstat(child_fd)
                        if (current.st_ino, current.st_dev, current.st_mode) != (opened.st_ino, opened.st_dev, opened.st_mode):
                            raise RuntimeError("snapshot_race")
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(mode):
                    entries[relative] = regular(item.name, budget, dir_fd=directory_fd)[0]
                elif stat.S_ISLNK(mode):
                    raise RuntimeError("symlink_unsupported")
                else:
                    raise RuntimeError("special_entry_unsupported")
        after = os.fstat(directory_fd)
        if (before.st_ino, before.st_dev, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino, after.st_dev, after.st_mtime_ns, after.st_ctime_ns):
            raise RuntimeError("snapshot_race")

    root_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        visit(root_fd, "")
        current = root.lstat()
        opened = os.fstat(root_fd)
        if (current.st_ino, current.st_dev, current.st_mode) != (opened.st_ino, opened.st_dev, opened.st_mode):
            raise RuntimeError("snapshot_race")
    finally:
        os.close(root_fd)
    budget.check()
    return entries


def git_binary():
    # Discover only from fixed system directories, never inherited PATH.
    found = shutil.which("git", path="/usr/bin:/bin")
    if not found:
        raise RuntimeError("git_unavailable")
    return safe_path(Path(found).resolve())


def validate_git_directory_names(entries):
    # Inspect actual inventory spelling; path lookup may ignore case on this host.
    for path in entries:
        root_name = path.split("/", 1)[0]
        if root_name.lower() == ".git" and root_name != ".git":
            raise RuntimeError("unsupported_git_directory_case")


def loose_object_entry(path, entry):
    """Only exact lowercase SHA-1 fanout directories and loose object files."""
    if not isinstance(entry, dict):
        return False
    if entry.get("kind") == "directory":
        return re.fullmatch(r"\.git/objects/[0-9a-f]{2}", path) is not None
    if entry.get("kind") == "file":
        return re.fullmatch(r"\.git/objects/[0-9a-f]{2}/[0-9a-f]{38}", path) is not None
    return False


def validate_loose_bytes(compressed, oid, budget):
    """Bounded wrapper/hash validation, not semantic fsck or authentication."""
    inflater = zlib.decompressobj()
    sha = hashlib.sha1()
    header = bytearray()
    declared = None
    body_size = 0
    offset = 0
    pending = b""
    try:
        while pending or offset < len(compressed):
            budget.check()
            if inflater.eof:
                raise RuntimeError("invalid_git_object")
            if not pending:
                pending = compressed[offset:offset + 65536]
                offset += len(pending)
            # Do not inflate an unbounded body while looking for its header.
            allowance = (OBJECT_HEADER_LIMIT + 1 - len(header) if declared is None
                         else min(65536, declared + 1 - body_size))
            output = inflater.decompress(pending, allowance)
            pending = inflater.unconsumed_tail
            budget.bytes += len(output)
            budget.check()
            sha.update(output)
            if declared is None:
                header.extend(output)
                end = header.find(0)
                if end < 0:
                    if len(header) > OBJECT_HEADER_LIMIT:
                        raise RuntimeError("invalid_git_object")
                else:
                    raw = bytes(header[:end])
                    match = re.fullmatch(rb"(blob|tree|commit|tag) (0|[1-9][0-9]*)", raw)
                    if end + 1 > OBJECT_HEADER_LIMIT or match is None:
                        raise RuntimeError("invalid_git_object")
                    declared = int(match.group(2))
                    body_size = len(header) - end - 1
                    if declared > FILE_LIMIT:
                        raise RuntimeError("git_object_limit")
                    if budget.bytes + declared - body_size > AGGREGATE_LIMIT:
                        raise RuntimeError("snapshot_limit")
                    header.clear()
            else:
                body_size += len(output)
            if declared is not None and body_size > declared:
                raise RuntimeError("invalid_git_object")
            if inflater.unused_data:
                raise RuntimeError("invalid_git_object")
    except zlib.error:
        raise RuntimeError("invalid_git_object") from None
    budget.check()
    if not inflater.eof or declared is None or body_size != declared:
        raise RuntimeError("invalid_git_object")
    if sha.hexdigest() != oid:
        raise RuntimeError("git_object_identity_mismatch")


def validate_git_objects(repo, entries, budget):
    """Loose-only input envelope, checked before Git consumes repository data."""
    if entries.get(".git/objects", {}).get("kind") != "directory":
        raise RuntimeError("unsupported_git_object_layout")
    objects = []
    for path, entry in entries.items():
        budget.check()
        if not path.startswith(".git/objects/"):
            continue
        if path in (".git/objects/info/alternates", ".git/objects/info/http-alternates"):
            raise RuntimeError("unsupported_git_control")
        if path in (".git/objects/info", ".git/objects/pack") and entry["kind"] == "directory":
            continue
        if not loose_object_entry(path, entry):
            raise RuntimeError("unsupported_git_object_layout")
        if entry["kind"] == "file":
            objects.append(path)

    def open_directory(name, parent):
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode):
            os.close(fd)
            raise RuntimeError("snapshot_race")
        return fd

    root_fd = os.open(str(repo), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        git_fd = open_directory(".git", root_fd)
        try:
            objects_fd = open_directory("objects", git_fd)
            try:
                for path in objects:
                    budget.check()
                    fanout, name = path.split("/")[-2:]
                    directory_fd = open_directory(fanout, objects_fd)
                    try:
                        identity, compressed = regular(name, budget, content=True, dir_fd=directory_fd)
                        if identity != entries[path]:
                            raise RuntimeError("snapshot_race")
                        validate_loose_bytes(compressed, fanout + name, budget)
                    finally:
                        os.close(directory_fd)
            finally:
                os.close(objects_fd)
        finally:
            os.close(git_fd)
    finally:
        os.close(root_fd)


def validate_git(repo, git):
    dot = repo / ".git"
    if not dot.is_dir() or dot.is_symlink():
        raise RuntimeError("ordinary_git_directory_required")
    for forbidden in ("commondir", "gitdir", "worktrees", "modules", "shallow", "config.worktree",
                      "info/grafts", "refs/replace", "objects/info/http-alternates"):
        if (dot / forbidden).exists():
            raise RuntimeError("unsupported_git_control")
    if (dot / "objects" / "info" / "alternates").exists():
        raise RuntimeError("unsupported_git_control")
    config_bytes = regular(dot / "config", Budget(), 65536, True)[1]
    try:
        parser = configparser.RawConfigParser(strict=True)
        parser.read_string(config_bytes.decode("utf-8"))
        if parser.defaults():
            raise RuntimeError("unsupported_git_config")
        allowed_core = {"repositoryformatversion", "filemode", "bare", "logallrefupdates",
                        "ignorecase", "precomposeunicode"}
        for section in parser.sections():
            fields = set(parser[section])
            if section == "core":
                if fields - allowed_core:
                    raise RuntimeError("unsupported_git_config")
                if parser[section].get("repositoryformatversion") != "0" or parser[section].get("bare") != "false":
                    raise RuntimeError("unsupported_git_config")
                if any(value not in ("true", "false") for key, value in parser[section].items()
                       if key not in ("repositoryformatversion", "bare")):
                    raise RuntimeError("unsupported_git_config")
            elif section.startswith('remote "') and section.endswith('"'):
                if fields - {"url", "fetch"}:
                    raise RuntimeError("unsupported_git_config")
            elif section.startswith('branch "') and section.endswith('"'):
                if fields - {"remote", "merge"}:
                    raise RuntimeError("unsupported_git_config")
            else:
                raise RuntimeError("unsupported_git_config")
    except (ValueError, UnicodeError, configparser.Error):
        raise RuntimeError("unsupported_git_config") from None
    if (repo / ".gitmodules").exists():
        raise RuntimeError("submodules_unsupported")
    hooks = dot / "hooks"
    if hooks.exists():
        with os.scandir(hooks) as scan:
            if any(not item.name.endswith(".sample") for item in scan):
                raise RuntimeError("active_hooks_unsupported")
    head = runner.git_read(git, repo, ("rev-parse", "--verify", "HEAD"))
    if not head.strip() or len(head.strip()) != 40:
        raise RuntimeError("existing_head_required")
    stages = runner.git_read(git, repo, ("ls-files", "--stage", "-z"))
    if any(line.startswith(b"160000 ") for line in stages.split(b"\0")):
        raise RuntimeError("submodules_unsupported")
    flags = runner.git_read(git, repo, ("ls-files", "-v", "-z"))
    if any(line and not line.startswith(b"H ") for line in flags.split(b"\0")):
        raise RuntimeError("unsupported_index_flags")
    index_entries = {}
    for line in stages.split(b"\0"):
        if not line:
            continue
        try:
            metadata, path = line.split(b"\t", 1)
            name = path.decode("utf-8", errors="strict")
        except (ValueError, UnicodeError):
            raise RuntimeError("unsupported_index_path") from None
        if name.startswith("/") or any(part in ("", ".", "..") or part.lower() == ".git"
                                       for part in name.split("/")):
            raise RuntimeError("unsupported_index_path")
        index_entries.setdefault(name, []).append(hashlib.sha256(metadata).hexdigest())
    refs = runner.git_read(git, repo, ("show-ref", "--head", "-d"))
    return {"head": head.strip().decode("ascii"), "stages": hashlib.sha256(stages).hexdigest(),
            "flags": hashlib.sha256(flags).hexdigest(), "refs": hashlib.sha256(refs).hexdigest(),
            "index_entries": index_entries}


def source_bindings(repo, sources, entries, index_entries, budget):
    """Bind exact inventory names; record index aliases using this filesystem only."""
    bindings = {}
    root_fd = os.open(str(repo), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        def identity(path):
            # Index names are prevalidated relative paths. Never follow a link,
            # including in a parent component, while probing filesystem aliases.
            directory_fd = os.dup(root_fd)
            try:
                parts = path.split("/")
                for part in parts[:-1]:
                    child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=directory_fd)
                    os.close(directory_fd)
                    directory_fd = child_fd
                info = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    return None
                return info.st_dev, info.st_ino
            except (FileNotFoundError, NotADirectoryError):
                return None
            finally:
                os.close(directory_fd)

        for source in sources:
            budget.check()
            entry = entries.get(source)
            if entry is None or entry["kind"] != "file":
                raise RuntimeError("invalid_source_binding")
            source_identity = identity(source)
            if source_identity is None:
                raise RuntimeError("invalid_source_binding")
            related = {}
            for path, stages in index_entries.items():
                budget.check()
                if (path == source or path.casefold() == source.casefold() or
                        identity(path) == source_identity):
                    related[path] = stages
            bindings[source] = {"worktree": entry, "index_entries": related}
    finally:
        os.close(root_fd)
    return bindings


def capture(repo, contract_path, sources=()):
    budget = Budget()
    repo = safe_path(repo)
    git = git_binary()
    entries = inventory(repo, budget)
    validate_git_directory_names(entries)
    validate_git_objects(repo, entries, budget)
    controls = validate_git(repo, git)
    bindings = source_bindings(repo, sources, entries, controls["index_entries"], budget)
    # Inventory contains index, refs, config, hooks, ignore/attributes and objects.
    # Metadata read by Git must match the same complete inventory afterwards.
    if entries != inventory(repo, budget):
        raise RuntimeError("snapshot_race")
    program_root = Path(__file__).resolve().parents[1]
    program = {}
    for name in ("taskproof", "tests"):
        for path, value in inventory(program_root / name, budget).items():
            program[name + "/" + path] = value
    executable = regular(git, budget)[0]
    python = safe_path(Path(sys.executable).resolve())
    python_identity = regular(python, budget)[0]
    input_identity = regular(safe_path(contract_path), budget, 65536)[0]
    execution = {"preset": runner.PRESET, "argv": list(runner.CHECK_ARGS),
                 "cwd": str(repo), "env": runner.environment(), "timeout": runner.TIMEOUT,
                 "output_limit": runner.OUTPUT_LIMIT, "git": executable,
                 "program": program, "resources": runner.resource_profile(),
                 "git_path": str(git), "python": python_identity, "python_path": str(python),
                 "python_version": list(sys.version_info[:3])}
    snapshot = {"entries": entries, "controls": controls, "source_bindings": bindings, "execution": execution,
                "contract_input": input_identity}
    return snapshot
