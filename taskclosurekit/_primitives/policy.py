"""Strict snapshot validation and source/scope policy; no task lifecycle."""
from pathlib import Path
import re
from . import runner, snapshot
from .store import fields


def relative_path(value):
    if (not isinstance(value, str) or not value or len(value) > 256 or "\\" in value or
            value.startswith("/") or any(part in ("", ".", "..") or part.lower() == ".git"
                                        for part in value.split("/")) or
            any(ord(char) < 32 for char in value)):
        raise RuntimeError("invalid_relative_path")
    return value



def hash_string(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None



def valid_snapshot(value, contract=None):
    snap = value["snapshot"]
    fields(snap, ("entries", "controls", "source_bindings", "execution", "contract_input", *(("capture_budget",) if "capture_budget" in snap else ())))
    limits=None
    if "capture_budget" in snap:
        from ..integration.budgets import validate_budget
        limits=validate_budget(snap["capture_budget"])
    control_fields = ("head", "stages", "flags", "refs", "index_entries")
    if "git_layout" in snap["controls"]:
        fields(snap["controls"], (*control_fields, "git_layout", *(("head_ref",) if "head_ref" in snap["controls"] else ())))
        valid_git_layout(snap["controls"]["git_layout"], snap["entries"])
    else:
        # Historical strict snapshots retain their original shape. They are
        # never upgraded or re-bound to the new inspection implementation.
        fields(snap["controls"], control_fields)
    fields(snap["execution"], ("preset", "argv", "cwd", "env", "timeout", "output_limit", "git", "program", "resources",
                               "git_path", "python", "python_path", "python_version"))
    controls = snap["controls"]
    if "head_ref" in controls and controls["head_ref"] is not None:
        ref=controls["head_ref"]
        if (type(ref) is not str or not ref.startswith("refs/heads/") or len(ref)>1024 or
                any(part in ("", ".", "..") for part in ref.split("/")) or any(ord(c)<33 or c in "\\~^:?*[" for c in ref)):
            raise RuntimeError("invalid_git_identity")
    if not isinstance(controls["head"], str) or not re.fullmatch(r"[0-9a-f]{40}", controls["head"]):
        raise RuntimeError("invalid_git_identity")
    if any(not hash_string(controls[name]) for name in ("stages", "flags", "refs")):
        raise RuntimeError("invalid_git_identity")
    if not isinstance(controls["index_entries"], dict) or len(controls["index_entries"]) > snapshot.COUNT_LIMIT:
        raise RuntimeError("invalid_index_identity")
    for path, stages in controls["index_entries"].items():
        relative_path(path)
        if not isinstance(stages, list) or not 1 <= len(stages) <= 3 or any(not hash_string(stage) for stage in stages):
            raise RuntimeError("invalid_index_identity")
    execution = snap["execution"]
    if ("git_layout" in controls and
            controls["git_layout"]["worktree"]["path"] != execution["cwd"]):
        raise RuntimeError("invalid_git_layout")
    if (execution["preset"] != runner.PRESET or execution["argv"] != list(runner.CHECK_ARGS) or
            execution["env"] != runner.environment() or execution["timeout"] != runner.TIMEOUT or
            execution["output_limit"] != runner.OUTPUT_LIMIT or execution["resources"] != runner.resource_profile()):
        # Stored known controls validate structurally; changing live preset requires new run.
        raise RuntimeError("execution_or_contract_changed")
    fields(execution["resources"], ("cpu_seconds", "file_bytes", "open_files", "core_bytes", "address_space_bytes", "platform"))
    for name in ("cpu_seconds", "file_bytes", "open_files", "core_bytes"):
        if type(execution["resources"][name]) is not int:
            raise RuntimeError("invalid_resource_profile")
    memory = execution["resources"]["address_space_bytes"]
    if memory is not None and type(memory) is not int:
        raise RuntimeError("invalid_resource_profile")
    if type(execution["output_limit"]) is not int or type(execution["timeout"]) not in (int, float):
        raise RuntimeError("invalid_runner_profile")
    if not isinstance(execution["python_version"], list) or len(execution["python_version"]) != 3 or any(type(part) is not int for part in execution["python_version"]):
        raise RuntimeError("invalid_python_identity")
    for name in ("cwd", "git_path", "python_path"):
        if not isinstance(execution[name], str) or not execution[name].startswith("/"):
            raise RuntimeError("invalid_execution_path")
    if not isinstance(snap["entries"], dict) or len(snap["entries"]) > (limits["entries"] if limits else snapshot.COUNT_LIMIT):
        raise RuntimeError("invalid_snapshot_entries")
    for entries in (snap["entries"], snap["execution"]["program"]):
        if not isinstance(entries, dict) or len(entries) > (limits["entries"] if limits else snapshot.COUNT_LIMIT):
            raise RuntimeError("invalid_snapshot_entries")
        for path, entry in entries.items():
            # Git paths are inventory data; they may contain .git but no traversal.
            if not isinstance(path, str) or path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/")):
                raise RuntimeError("invalid_snapshot_path")
            valid_entry(entry,limits["file_bytes"] if limits else snapshot.FILE_LIMIT)
    snapshot.validate_git_directory_names(snap["entries"])
    if contract is not None:
        valid_sources(contract, snap)
    valid_entry(snap["contract_input"])
    valid_entry(snap["execution"]["git"])
    valid_entry(snap["execution"]["python"])
    if not hash_string(value["digest"]) or snapshot.digest(snap) != value["digest"]:
        raise RuntimeError("snapshot_binding_mismatch")



def valid_git_layout(layout, entries):
    fields(layout, ("schema", "kind", "worktree", "git_dir", "common_dir", "link_bindings",
                    "object_format", "index_format", "hooks_root"))
    if (layout["schema"] != "git-layout/v1" or layout["kind"] not in ("ordinary", "linked") or
            layout["object_format"] != "sha1" or type(layout["index_format"]) is not int or
            layout["index_format"] != 2):
        raise RuntimeError("invalid_git_layout")
    for name in ("worktree", "git_dir", "common_dir", "hooks_root"):
        root = layout[name]
        fields(root, ("path", "exists", "device", "inode"))
        path = root["path"]
        if (not isinstance(path, str) or not path.startswith("/") or
                str(Path(path)) != path or any(part in (".", "..") for part in path.split("/")) or
                type(root["exists"]) is not bool):
            raise RuntimeError("invalid_git_layout")
        if root["exists"]:
            if any(type(root[key]) is not int or root[key] < 0 for key in ("device", "inode")):
                raise RuntimeError("invalid_git_layout")
        elif name != "hooks_root" or root["device"] is not None or root["inode"] is not None:
            raise RuntimeError("invalid_git_layout")
    links = layout["link_bindings"]
    fields(links, ("gitfile", "commondir", "backlink"))
    worktree, git_dir, common = (Path(layout[name]["path"]) for name in ("worktree", "git_dir", "common_dir"))
    if layout["kind"] == "ordinary":
        if (git_dir != worktree / ".git" or common != git_dir or
                layout["git_dir"] != layout["common_dir"] or
                any(value is not None for value in links.values())):
            raise RuntimeError("invalid_git_layout")
    else:
        if git_dir.parent != common / "worktrees":
            raise RuntimeError("invalid_git_layout")
        for name, path in (("gitfile", ".git"), ("commondir", ".git/commondir"), ("backlink", ".git/gitdir")):
            valid_entry(links[name])
            if links[name]["kind"] != "file" or entries.get(path) != links[name]:
                raise RuntimeError("invalid_git_layout")


def valid_sources(contract, snap):
    bindings = snap["source_bindings"]
    if not isinstance(bindings, dict) or set(bindings) != set(contract["sources"]):
        raise RuntimeError("invalid_source_binding")
    for source in contract["sources"]:
        entry = snap["entries"].get(source)
        if entry is None or entry["kind"] != "file":
            raise RuntimeError("invalid_source_binding")
        binding = bindings[source]
        fields(binding, ("worktree", "index_entries"))
        expected_index = ({source: snap["controls"]["index_entries"][source]}
                          if source in snap["controls"]["index_entries"] else {})
        if binding["worktree"] != entry or binding["index_entries"] != expected_index:
            raise RuntimeError("invalid_source_binding")
        if any(path != source and path.casefold() == source.casefold()
               for path in snap["controls"]["index_entries"]):
            raise RuntimeError("invalid_source_binding")



def valid_entry(entry, file_limit=snapshot.FILE_LIMIT):
    if not isinstance(entry, dict):
        raise RuntimeError("invalid_snapshot_entry")
    if entry.get("kind") == "file":
        fields(entry, ("kind", "mode", "size", "sha256"))
        if type(entry["size"]) is not int or not 0 <= entry["size"] <= file_limit or not hash_string(entry["sha256"]):
            raise RuntimeError("invalid_snapshot_entry")
    elif entry.get("kind") == "directory":
        fields(entry, ("kind", "mode"))
    else:
        raise RuntimeError("invalid_snapshot_entry")
    if type(entry["mode"]) is not int or not 0 <= entry["mode"] <= 0o7777:
        raise RuntimeError("invalid_snapshot_entry")



def scope_contains(path, scope):
    return any(path == owned or path.startswith(owned + "/") for owned in scope)



def permitted_change(contract, baseline, current):
    previous = baseline["snapshot"]
    valid_sources(contract, previous)
    valid_sources(contract, current)
    for name in ("execution", "contract_input"):
        if previous[name] != current[name]:
            raise RuntimeError("execution_or_contract_changed")
    for source in contract["sources"]:
        if previous["source_bindings"][source] != current["source_bindings"][source]:
            raise RuntimeError("source_changed")
    if contract["mode"] == "Review":
        if previous != current:
            raise RuntimeError("review_mode_drift")
        return
    if (previous["controls"].get("git_layout") != current["controls"].get("git_layout") or
            previous["controls"]["head"] != current["controls"]["head"] or
            previous["controls"]["refs"] != current["controls"]["refs"]):
        raise RuntimeError("git_authority_changed")
    old_index, new_index = previous["controls"]["index_entries"], current["controls"]["index_entries"]
    for path in set(old_index) | set(new_index):
        if old_index.get(path) == new_index.get(path):
            continue
        # Git also opens these names on case-insensitive filesystems.
        if Path(path).name.lower() == ".gitattributes":
            raise RuntimeError("git_control_changed")
        if not scope_contains(path, contract["scope"]):
            raise RuntimeError("outside_scope_index_changed")
    # Index + newly staged objects may change under Direct; all remaining controls freeze.
    paths = set(previous["entries"]) | set(current["entries"])
    for path in paths:
        old, new = previous["entries"].get(path), current["entries"].get(path)
        if old == new:
            continue
        if path == ".git/index":
            continue
        if old is None and snapshot.loose_object_entry(path, new):
            continue
        if path.startswith(".git/") or Path(path).name.lower() == ".gitattributes":
            raise RuntimeError("git_control_changed")
        if not scope_contains(path, contract["scope"]):
            raise RuntimeError("outside_scope_changed")
