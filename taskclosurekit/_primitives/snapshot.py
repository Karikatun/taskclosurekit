"""Fail-closed bounded snapshots including hidden, ignored and ordinary Git inputs."""

import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
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
    def __init__(self, limits=None):
        self.started = time.monotonic()
        self.count = 0
        self.bytes = 0
        self.limits = limits
        self.file_limit = limits["file_bytes"] if limits else FILE_LIMIT

    def check(self):
        if (self.count > (self.limits["entries"] if self.limits else COUNT_LIMIT) or
                self.bytes > (self.limits["aggregate_bytes"] if self.limits else AGGREGATE_LIMIT) or
                time.monotonic() - self.started > (self.limits["seconds"] if self.limits else SNAPSHOT_SECONDS)):
            raise RuntimeError("snapshot_limit")


def regular(path, budget, limit=None, content=False, dir_fd=None):
    """Read at most limit+1 using a non-following descriptor and identity recheck."""
    limit = budget.file_limit if limit is None and hasattr(budget,"file_limit") else FILE_LIMIT if limit is None else limit
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


def inventory(root, budget=None, *, metadata_exclusions=(), immutable_entries=None):
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
                    if immutable_entries and relative in immutable_entries:
                        binding=immutable_entries[relative]
                        child_fd=os.open(item.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory_fd)
                        try:
                            opened=os.fstat(child_fd)
                            current=os.stat(item.name,dir_fd=directory_fd,follow_symlinks=False)
                            expected=binding["physical"]
                            if any((entry.st_dev,entry.st_ino,stat.S_IMODE(entry.st_mode)) != (expected["device"],expected["inode"],expected["mode"]) for entry in (opened,current)):
                                raise RuntimeError("immutable_root_binding_mismatch")
                            cached=binding["entries"]
                            entries.update({relative+"/"+name:entry for name,entry in cached.items()})
                            budget.count+=len(cached);budget.check()
                        finally:os.close(child_fd)
                        continue
                    if relative in metadata_exclusions:
                        continue
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
        return re.fullmatch(r"\.git/(?:common/)?objects/[0-9a-f]{2}", path) is not None
    if entry.get("kind") == "file":
        return re.fullmatch(r"\.git/(?:common/)?objects/[0-9a-f]{2}/[0-9a-f]{38}", path) is not None
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


def directory_identity(path, *, optional=False):
    path = safe_path(path)
    try:
        before = path.lstat()
    except FileNotFoundError:
        if optional:
            return {"path": str(path), "exists": False, "device": None, "inode": None}
        raise RuntimeError("unsupported_git_layout") from None
    if not stat.S_ISDIR(before.st_mode):
        raise RuntimeError("unsupported_git_layout")
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode):
            raise RuntimeError("snapshot_race")
    finally:
        os.close(fd)
    return {"path": str(path), "exists": True, "device": before.st_dev, "inode": before.st_ino}


def metadata_path(raw, relative_to):
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeError:
        raise RuntimeError("unsupported_git_layout") from None
    if not text.endswith("\n") or "\n" in text[:-1] or not text[:-1] or "\0" in text:
        raise RuntimeError("unsupported_git_layout")
    value = Path(text[:-1])
    value = value if value.is_absolute() else relative_to / value
    return safe_path(Path(os.path.normpath(str(value))))


def git_layout(repo, budget):
    dot = repo / ".git"
    info = dot.lstat()
    links = {"gitfile": None, "commondir": None, "backlink": None}
    if stat.S_ISDIR(info.st_mode):
        kind = "ordinary"
        git_dir = common = safe_path(dot)
        if (dot / "commondir").exists() or (dot / "gitdir").exists():
            raise RuntimeError("unsupported_git_layout")
    elif stat.S_ISREG(info.st_mode):
        kind = "linked"
        links["gitfile"], raw = regular(dot, budget, 4096, True)
        if not raw.startswith(b"gitdir: "):
            raise RuntimeError("unsupported_git_layout")
        git_dir = metadata_path(raw[8:], repo)
        links["commondir"], raw = regular(git_dir / "commondir", budget, 4096, True)
        common = metadata_path(raw, git_dir)
        # Only Git's registered linked-worktree layout is accepted. An arbitrary
        # gitfile is not authority to inspect a different repository.
        if git_dir.parent != common / "worktrees":
            raise RuntimeError("unsupported_git_layout")
        links["backlink"], raw = regular(git_dir / "gitdir", budget, 4096, True)
        if metadata_path(raw, git_dir) != dot:
            raise RuntimeError("git_worktree_identity_mismatch")
    else:
        raise RuntimeError("unsupported_git_layout")
    return {"schema": "git-layout/v1", "kind": kind,
            "worktree": directory_identity(repo), "git_dir": directory_identity(git_dir),
            "common_dir": directory_identity(common), "link_bindings": links,
            "object_format": "sha1", "index_format": 2, "hooks_root": None}


def metadata_inventory(repo, layout, budget, immutable_entries=None):
    entries = inventory(repo, budget, metadata_exclusions=(".git/worktrees",),immutable_entries=immutable_entries)
    if any(path == ".git/hooks-bound" or path.startswith(".git/hooks-bound/") for path in entries):
        raise RuntimeError("unsupported_git_layout")
    if layout["kind"] == "linked":
        git_dir = Path(layout["git_dir"]["path"])
        common = Path(layout["common_dir"]["path"])
        for prefix, root in ((".git/", git_dir), (".git/common/", common)):
            excluded = ("worktrees",) if root == common else ()
            for path, entry in inventory(root, budget, metadata_exclusions=excluded).items():
                key = prefix + path
                if key in entries:
                    raise RuntimeError("unsupported_git_layout")
                entries[key] = entry
    hooks = layout.get("hooks_root")
    if hooks and hooks["exists"]:
        for path, entry in inventory(Path(hooks["path"]), budget).items():
            entries[".git/hooks-bound/" + path] = entry
    return entries


def validate_pack_bytes(data, expected_oid, budget):
    """Bound the pack envelope and delta result sizes before a Git consumer."""
    if len(data) < 32 or data[:4] != b"PACK":
        raise RuntimeError("invalid_git_object")
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3) or count > COUNT_LIMIT:
        raise RuntimeError("unsupported_git_pack_format")
    if hashlib.sha1(data[:-20]).digest() != data[-20:] or data[-20:].hex() != expected_oid:
        raise RuntimeError("git_object_identity_mismatch")
    offset, end = 12, len(data) - 20
    for unused in range(count):
        budget.check()
        if offset >= end:
            raise RuntimeError("invalid_git_object")
        first = data[offset]; offset += 1
        object_type, size, shift, byte = (first >> 4) & 7, first & 15, 4, first
        while byte & 128:
            if offset >= end or shift > 63:
                raise RuntimeError("invalid_git_object")
            byte = data[offset]; offset += 1
            size |= (byte & 127) << shift; shift += 7
        if object_type not in (1, 2, 3, 4, 6, 7):
            raise RuntimeError("invalid_git_object")
        if size > FILE_LIMIT:
            raise RuntimeError("git_object_limit")
        if object_type == 6:
            if offset >= end:
                raise RuntimeError("invalid_git_object")
            byte = data[offset]; offset += 1
            while byte & 128:
                if offset >= end:
                    raise RuntimeError("invalid_git_object")
                byte = data[offset]; offset += 1
        elif object_type == 7:
            offset += 20
        if offset >= end:
            raise RuntimeError("invalid_git_object")
        inflater, body = zlib.decompressobj(), bytearray()
        total = 0
        try:
            cursor, pending = offset, b""
            while not inflater.eof:
                budget.check()
                if not pending:
                    pending = data[cursor:min(cursor + 65536, end)]
                    cursor += len(pending)
                output = inflater.decompress(pending, min(65536, size + 1 - total))
                pending = inflater.unconsumed_tail
                total += len(output); budget.bytes += len(output); budget.check()
                if total > size:
                    raise RuntimeError("invalid_git_object")
                if object_type in (6, 7):
                    body.extend(output)
                if not output and not pending and not inflater.eof:
                    raise RuntimeError("invalid_git_object")
        except zlib.error:
            raise RuntimeError("invalid_git_object") from None
        if total != size:
            raise RuntimeError("invalid_git_object")
        # unused_data begins at the next pack record, including when max_length
        # caused multiple bounded inflate calls.
        offset = cursor - len(inflater.unused_data)
        if object_type in (6, 7):
            position = 0
            for ignored in range(2):
                value = 0; shift = 0
                while True:
                    if position >= len(body) or shift > 63:
                        raise RuntimeError("invalid_git_object")
                    byte = body[position]; position += 1
                    value |= (byte & 127) << shift; shift += 7
                    if not byte & 128:
                        break
                if value > FILE_LIMIT:
                    raise RuntimeError("git_object_limit")
    if offset != end:
        raise RuntimeError("invalid_git_object")


def validate_pack_index(data, pack_oid):
    if len(data) < 8 + 1024 + 40 or data[:8] != b"\xfftOc\x00\x00\x00\x02":
        raise RuntimeError("unsupported_git_pack_index_format")
    fanout = struct.unpack(">256I", data[8:1032])
    if any(a > b for a, b in zip(fanout, fanout[1:])) or fanout[-1] > COUNT_LIMIT:
        raise RuntimeError("invalid_git_object")
    count = fanout[-1]
    minimum = 1032 + count * 28 + 40
    if len(data) < minimum or (len(data) - minimum) % 8:
        raise RuntimeError("invalid_git_object")
    if data[-40:-20].hex() != pack_oid or hashlib.sha1(data[:-20]).digest() != data[-20:]:
        raise RuntimeError("git_object_identity_mismatch")


def object_regular(common, relative, budget):
    """Open every object ancestor without following links, including under races."""
    path = common / "objects" / relative
    parent = Path(path.anchor)
    fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    observed = []
    try:
        for component in path.parts[1:-1]:
            budget.check()
            before = os.stat(component, dir_fd=fd, follow_symlinks=False)
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            opened = os.fstat(child)
            if (before.st_dev, before.st_ino, before.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode):
                os.close(child)
                raise RuntimeError("snapshot_race")
            parent = parent / component
            observed.append((parent, opened.st_dev, opened.st_ino, opened.st_mode))
            os.close(fd); fd = child
        result = regular(path.name, budget, content=True, dir_fd=fd)
        for parent, device, inode, mode in observed:
            now = parent.lstat()
            if (now.st_dev, now.st_ino, now.st_mode) != (device, inode, mode):
                raise RuntimeError("snapshot_race")
        return result
    finally:
        os.close(fd)


def validate_git_objects(repo, entries, budget, layout=None):
    layout = layout or git_layout(repo, budget)
    common = Path(layout["common_dir"]["path"])
    prefix = ".git/common/objects" if layout["kind"] == "linked" else ".git/objects"
    if entries.get(prefix, {}).get("kind") != "directory":
        raise RuntimeError("unsupported_git_object_layout")
    packs = {}
    for path, entry in entries.items():
        budget.check()
        if not path.startswith(prefix + "/"):
            continue
        relative = path[len(prefix) + 1:]
        if relative in ("info/alternates", "info/http-alternates"):
            raise RuntimeError("unsupported_git_control")
        if relative in ("info", "pack") and entry["kind"] == "directory":
            continue
        if relative == "info/packs" and entry["kind"] == "file":
            # Local dumb-transport advertisement is inert inspection metadata.
            # No transport operation uses it; its bytes remain bound controls.
            continue
        if loose_object_entry(path, entry):
            if entry["kind"] == "file":
                identity, compressed = object_regular(common, relative, budget)
                if identity != entry:
                    raise RuntimeError("snapshot_race")
                validate_loose_bytes(compressed, relative.replace("/", ""), budget)
            continue
        match = re.fullmatch(r"pack/pack-([0-9a-f]{40})\.(pack|idx|rev)", relative)
        if match is None or entry["kind"] != "file":
            raise RuntimeError("unsupported_git_object_layout")
        pack_oid, kind = match.groups()
        identity, data = object_regular(common, relative, budget)
        if identity != entry:
            raise RuntimeError("snapshot_race")
        packs.setdefault(pack_oid, set()).add(kind)
        if kind == "pack":
            validate_pack_bytes(data, pack_oid, budget)
        elif kind == "idx":
            validate_pack_index(data, pack_oid)
        elif (len(data) < 52 or data[:12] != b"RIDX\x00\x00\x00\x01\x00\x00\x00\x01" or
              data[-40:-20].hex() != pack_oid or hashlib.sha1(data[:-20]).digest() != data[-20:]):
            raise RuntimeError("unsupported_git_pack_reverse_index_format")
    if any(not {"pack", "idx"} <= kinds for kinds in packs.values()):
        raise RuntimeError("unsupported_git_object_layout")


def validate_index(git_dir, budget):
    identity, data = regular(git_dir / "index", budget, content=True)
    if len(data) < 32 or data[:4] != b"DIRC":
        raise RuntimeError("unsupported_git_index_format")
    version, count = struct.unpack(">II", data[4:12])
    if version != 2:
        raise RuntimeError("unsupported_git_index_format")
    if count > COUNT_LIMIT or hashlib.sha1(data[:-20]).digest() != data[-20:]:
        raise RuntimeError("invalid_git_index")
    offset, end = 12, len(data) - 20
    for ignored in range(count):
        budget.check()
        start = offset
        if offset + 62 > end:
            raise RuntimeError("invalid_git_index")
        flags = struct.unpack(">H", data[offset + 60:offset + 62])[0]
        if flags & 0x4000:
            raise RuntimeError("unsupported_git_index_format")
        name_end = data.find(b"\0", offset + 62, end)
        if name_end < 0:
            raise RuntimeError("invalid_git_index")
        offset = start + ((name_end + 1 - start + 7) // 8) * 8
    while offset < end:
        if offset + 8 > end:
            raise RuntimeError("invalid_git_index")
        signature = data[offset:offset + 4]
        size = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        if signature not in (b"TREE", b"REUC", b"EOIE", b"IEOT"):
            raise RuntimeError("unsupported_git_index_extension")
        offset += 8 + size
    if offset != end:
        raise RuntimeError("invalid_git_index")
    return identity


def validate_git(repo, git, layout=None, budget=None, *, inspect=True):
    budget = budget or Budget()
    layout = layout or git_layout(repo, budget)
    dot = Path(layout["common_dir"]["path"])
    git_dir = Path(layout["git_dir"]["path"])
    for forbidden in ("modules", "shallow", "config.worktree", "info/grafts", "refs/replace",
                      "objects/info/http-alternates", "objects/info/alternates"):
        if (dot / forbidden).exists() or (git_dir / forbidden).exists():
            raise RuntimeError("unsupported_git_control")
    if (dot / "reftable").exists():
        raise RuntimeError("unsupported_git_ref_format")
    config_bytes = regular(dot / "config", budget, 65536, True)[1]
    try:
        parser = configparser.RawConfigParser(strict=True)
        parser.read_string(config_bytes.decode("utf-8"))
        if parser.defaults():
            raise RuntimeError("unsupported_git_config")
        if parser.has_section("extensions"):
            if parser["extensions"].get("objectformat", "sha1") != "sha1":
                raise RuntimeError("unsupported_git_object_format")
            if parser["extensions"].get("refstorage", "files") != "files":
                raise RuntimeError("unsupported_git_ref_format")
            raise RuntimeError("unsupported_git_config")
        allowed_core = {"repositoryformatversion", "filemode", "bare", "logallrefupdates",
                        "ignorecase", "precomposeunicode", "hookspath"}
        for section in parser.sections():
            fields = set(parser[section])
            if section == "core":
                if fields - allowed_core:
                    raise RuntimeError("unsupported_git_config")
                if parser[section].get("repositoryformatversion") != "0" or parser[section].get("bare") != "false":
                    raise RuntimeError("unsupported_git_config")
                if any(value not in ("true", "false") for key, value in parser[section].items()
                       if key not in ("repositoryformatversion", "bare", "hookspath")):
                    raise RuntimeError("unsupported_git_config")
            elif section.startswith('remote "') and section.endswith('"'):
                if fields - {"url", "fetch"}:
                    raise RuntimeError("unsupported_git_config")
            elif section.startswith('branch "') and section.endswith('"'):
                if fields - {"remote", "merge"}:
                    raise RuntimeError("unsupported_git_config")
            else:
                raise RuntimeError("unsupported_git_config")
        hooks_path = parser["core"].get("hookspath")
        if hooks_path is None:
            hooks = dot / "hooks"
        else:
            # No shell, tilde or Git path interpolation. Canonical physical paths
            # are the supported explicit hooks policy.
            # RawConfigParser does not implement Git quoting/escape/comment rules.
            # Fail closed for those spellings instead of binding a different path.
            if (not hooks_path or hooks_path.startswith("~") or "\0" in hooks_path or
                    any(c in hooks_path for c in ('"', "'", "\\", "#", ";"))):
                raise RuntimeError("unsupported_git_config")
            hooks = Path(hooks_path)
            hooks = hooks if hooks.is_absolute() else repo / hooks
            hooks = Path(os.path.normpath(str(hooks)))
        layout["hooks_root"] = directory_identity(hooks, optional=True)
    except (ValueError, UnicodeError, configparser.Error):
        raise RuntimeError("unsupported_git_config") from None
    if (repo / ".gitmodules").exists():
        raise RuntimeError("submodules_unsupported")
    validate_index(git_dir, budget)
    if not inspect:
        return
    # These commands have no hook or diff driver dispatch. The sanitized runner
    # does not override hooksPath; presence/content of hooks remain controls.
    runner.git_read(git, repo, ("fsck", "--strict", "--no-reflogs"))
    head = runner.git_read(git, repo, ("rev-parse", "--verify", "HEAD"))
    if re.fullmatch(rb"[0-9a-f]{40}\n", head) is None:
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
    head_bytes = regular(git_dir / "HEAD", budget, 65536, True)[1]
    if head_bytes.startswith(b"ref: "):
        try:
            head_ref = head_bytes[5:].strip().decode("ascii")
        except UnicodeError:
            raise RuntimeError("unsupported_git_ref_format") from None
        if (not head_ref.startswith("refs/heads/") or len(head_ref)>1024 or
                any(part in ("", ".", "..") for part in head_ref.split("/")) or
                any(ord(c)<33 or c in "\\~^:?*[" for c in head_ref)):
            raise RuntimeError("unsupported_git_ref_format")
    else:
        head_ref = None
    return {"head": head.strip().decode("ascii"), "stages": hashlib.sha256(stages).hexdigest(),
            "flags": hashlib.sha256(flags).hexdigest(), "refs": hashlib.sha256(refs).hexdigest(),
            "index_entries": index_entries, "git_layout": layout, "head_ref":head_ref}


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


def capture(repo, contract_path, sources=(), *, program_roots=("taskclosurekit", "tests"), capture_budget=None, immutable_entries=None):
    if capture_budget:
        from ..integration.budgets import validate_budget
        validate_budget(capture_budget)
    budget = Budget(capture_budget)
    repo = safe_path(repo)
    git = git_binary()
    # Classify the actual root spelling before lookup on case-insensitive hosts.
    with os.scandir(repo) as scan:
        root_names = {}
        for item in scan:
            budget.count += 1; budget.check()
            root_names[item.name] = None
    validate_git_directory_names(root_names)
    layout = git_layout(repo, budget)
    validate_git(repo, git, layout, budget, inspect=False)
    initial_layout = json.loads(json.dumps(layout))
    entries = metadata_inventory(repo, layout, budget,immutable_entries)
    validate_git_directory_names(entries)
    validate_git_objects(repo, entries, budget, layout)
    controls = validate_git(repo, git, layout, budget)
    if layout != initial_layout:
        raise RuntimeError("snapshot_race")
    bindings = source_bindings(repo, sources, entries, controls["index_entries"], budget)
    # Inventory contains index, refs, config, hooks, ignore/attributes and objects.
    # Metadata read by Git must match the same complete inventory afterwards.
    if entries != metadata_inventory(repo, layout, budget,immutable_entries):
        raise RuntimeError("snapshot_race")
    current_layout = git_layout(repo, budget)
    current_layout["hooks_root"] = directory_identity(Path(layout["hooks_root"]["path"]), optional=True)
    if current_layout != layout:
        raise RuntimeError("snapshot_race")
    program_root = Path(__file__).resolve().parents[2]
    program = {}
    for name in program_roots:
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
    if capture_budget:
        snapshot["capture_budget"]=capture_budget
    return snapshot
