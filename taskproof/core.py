"""One bounded lifecycle, with authority supplied separately from check output."""

from pathlib import Path
import re
import time

from . import runner, snapshot
from .store import Store, bounded_json, fields


CONTRACT_FIELDS = ("schema", "run_id", "repo", "mode", "scope", "actions", "sources",
                   "preset", "acceptance", "review")


def relative_path(value):
    if (not isinstance(value, str) or not value or len(value) > 256 or "\\" in value or
            value.startswith("/") or any(part in ("", ".", "..") or part.lower() == ".git"
                                        for part in value.split("/")) or
            any(ord(char) < 32 for char in value)):
        raise RuntimeError("invalid_relative_path")
    return value


def contract_valid(value):
    fields(value, CONTRACT_FIELDS)
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise RuntimeError("unsupported_schema")
    if not isinstance(value["run_id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value["run_id"]):
        raise RuntimeError("invalid_run_id")
    if value["mode"] == "TDD-first":
        raise RuntimeError("tdd_first_unsupported")
    if value["mode"] not in ("Review", "Direct", "Investigation"):
        raise RuntimeError("unsupported_mode")
    for name in ("scope", "sources"):
        items = value[name]
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise RuntimeError("invalid_contract_paths")
        for item in items:
            relative_path(item)
        if len(set(items)) != len(items):
            raise RuntimeError("duplicate_contract_paths")
    if value["actions"] != ["snapshot", "check", "review", "close"]:
        raise RuntimeError("unsupported_actions")
    if value["preset"] != runner.PRESET or value["acceptance"] != ["staged-whitespace"]:
        raise RuntimeError("unsupported_check_contract")
    fields(value["review"], ("required", "independence"))
    if value["review"]["required"] is not True or value["review"]["independence"] not in ("required", "not_required"):
        raise RuntimeError("unsupported_review_contract")
    if not isinstance(value["repo"], str):
        raise RuntimeError("invalid_repository")
    repo = snapshot.safe_path(value["repo"])
    if not repo.is_dir():
        raise RuntimeError("repository_required")
    for source in value["sources"]:
        if not (repo / source).is_file():
            raise RuntimeError("missing_source")
    return value


def create(store_path, input_path):
    input_path = snapshot.safe_path(input_path)
    value = contract_valid(bounded_json(input_path))
    store = Store(store_path, value["repo"])
    if snapshot.path_within(input_path, value["repo"]):
        raise RuntimeError("contract_input_must_be_outside_repository")
    if snapshot.path_within(input_path, store.path):
        raise RuntimeError("contract_input_must_be_outside_store")
    with store.locked(create=True):
        store.initialize()
        store.append([], "DRAFT", {"contract": value, "input_path": str(input_path),
                                    "input_hash": snapshot.regular(input_path, snapshot.Budget(), 65536)[0]}, value["run_id"])
    return {"state": "DRAFT", "run_id": value["run_id"], "next": "baseline"}


def validate_events(events):
    """HMAC authenticates bytes; this separately validates their meanings and bindings."""
    draft = events[0]
    if draft["kind"] != "DRAFT":
        raise RuntimeError("invalid_initial_state")
    fields(draft["payload"], ("contract", "input_path", "input_hash"))
    valid_entry(draft["payload"]["input_hash"])
    if not isinstance(draft["payload"]["input_path"], str):
        raise RuntimeError("invalid_contract_input_path")
    contract = contract_valid(draft["payload"]["contract"])
    if contract["run_id"] != draft["run_id"]:
        raise RuntimeError("store_binding_mismatch")
    state = "DRAFT"
    baseline = None
    running = None
    checked = None
    reviewed = None
    last_confirmed = "DRAFT"
    for event in events[1:]:
        kind, payload = event["kind"], event["payload"]
        if kind == "BASELINED" and state == "DRAFT":
            fields(payload, ("snapshot", "digest"))
            valid_snapshot(payload, contract)
            baseline = payload
            last_confirmed = kind
        elif kind == "RUNNING" and state in ("BASELINED", "CHECKED", "REVIEWED", "BLOCKED"):
            fields(payload, ("snapshot", "digest", "started_ns"))
            valid_snapshot(payload, contract)
            if type(payload["started_ns"]) is not int or payload["started_ns"] <= 0:
                raise RuntimeError("invalid_execution_time")
            running = payload
        elif kind in ("CHECKED", "BLOCKED") and state == "RUNNING":
            fields(payload, ("snapshot_digest", "baseline_digest", "journal", "reason", "finished_ns"))
            fields(payload["journal"], ("kind", "stdout_bytes", "stderr_bytes", "exit_code", "timeout", "truncated", "duration_ms"))
            journal = payload["journal"]
            for field in ("stdout_bytes", "stderr_bytes", "duration_ms", "exit_code"):
                if type(journal[field]) is not int:
                    raise RuntimeError("invalid_execution_journal")
            if (journal["kind"] != "discarded-output-v1" or type(journal["timeout"]) is not bool or
                    type(journal["truncated"]) is not bool or any(journal[x] < 0 for x in
                    ("stdout_bytes", "stderr_bytes", "duration_ms"))):
                raise RuntimeError("invalid_execution_journal")
            output_bytes = journal["stdout_bytes"] + journal["stderr_bytes"]
            if (not -64 <= journal["exit_code"] <= 255 or output_bytes > runner.OUTPUT_LIMIT + 8192 or
                    (not journal["truncated"] and output_bytes > runner.OUTPUT_LIMIT)):
                raise RuntimeError("invalid_execution_journal")
            if (payload["snapshot_digest"] != running["digest"] or payload["baseline_digest"] != baseline["digest"] or
                    type(payload["finished_ns"]) is not int or payload["finished_ns"] < running["started_ns"]):
                raise RuntimeError("receipt_binding_mismatch")
            if kind == "CHECKED":
                if journal["exit_code"] != 0 or journal["timeout"] or journal["truncated"] or payload["reason"] is not None:
                    raise RuntimeError("invalid_success_receipt")
                checked = {"event": payload, "snapshot": running["snapshot"], "digest": running["digest"]}
                reviewed = None
                last_confirmed = kind
            elif payload["reason"] not in ("check_failed", "check_timeout", "check_output_limit", "inputs_changed_during_check"):
                raise RuntimeError("invalid_block_reason")
        elif kind == "REVIEWED" and state == "CHECKED":
            fields(payload, ("snapshot_digest", "review_hash", "independence"))
            if payload["snapshot_digest"] != checked["digest"] or payload["independence"] != "not_required" or contract["review"]["independence"] != "not_required":
                raise RuntimeError("review_binding_mismatch")
            if not hash_string(payload["review_hash"]):
                raise RuntimeError("invalid_review_identity")
            reviewed = payload
            last_confirmed = kind
        elif kind == "CLOSED" and state == "REVIEWED":
            fields(payload, ("snapshot_digest",))
            if payload["snapshot_digest"] != reviewed["snapshot_digest"]:
                raise RuntimeError("close_binding_mismatch")
            last_confirmed = kind
        else:
            raise RuntimeError("invalid_state_sequence")
        state = kind
    return contract, state, last_confirmed, baseline, checked, reviewed


def hash_string(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def valid_snapshot(value, contract=None):
    snap = value["snapshot"]
    fields(snap, ("entries", "controls", "source_bindings", "execution", "contract_input"))
    fields(snap["controls"], ("head", "stages", "flags", "refs", "index_entries"))
    fields(snap["execution"], ("preset", "argv", "cwd", "env", "timeout", "output_limit", "git", "program", "resources",
                               "git_path", "python", "python_path", "python_version"))
    controls = snap["controls"]
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
    if not isinstance(snap["entries"], dict) or len(snap["entries"]) > snapshot.COUNT_LIMIT:
        raise RuntimeError("invalid_snapshot_entries")
    for entries in (snap["entries"], snap["execution"]["program"]):
        if not isinstance(entries, dict) or len(entries) > snapshot.COUNT_LIMIT:
            raise RuntimeError("invalid_snapshot_entries")
        for path, entry in entries.items():
            # Git paths are inventory data; they may contain .git but no traversal.
            if not isinstance(path, str) or path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/")):
                raise RuntimeError("invalid_snapshot_path")
            valid_entry(entry)
    snapshot.validate_git_directory_names(snap["entries"])
    if contract is not None:
        valid_sources(contract, snap)
    valid_entry(snap["contract_input"])
    valid_entry(snap["execution"]["git"])
    valid_entry(snap["execution"]["python"])
    if not hash_string(value["digest"]) or snapshot.digest(snap) != value["digest"]:
        raise RuntimeError("snapshot_binding_mismatch")


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


def valid_entry(entry):
    if not isinstance(entry, dict):
        raise RuntimeError("invalid_snapshot_entry")
    if entry.get("kind") == "file":
        fields(entry, ("kind", "mode", "size", "sha256"))
        if type(entry["size"]) is not int or not 0 <= entry["size"] <= snapshot.FILE_LIMIT or not hash_string(entry["sha256"]):
            raise RuntimeError("invalid_snapshot_entry")
    elif entry.get("kind") == "directory":
        fields(entry, ("kind", "mode"))
    else:
        raise RuntimeError("invalid_snapshot_entry")
    if type(entry["mode"]) is not int or not 0 <= entry["mode"] <= 0o7777:
        raise RuntimeError("invalid_snapshot_entry")


def unchanged_authority(draft, current):
    if current["contract_input"] != draft["payload"]["input_hash"]:
        raise RuntimeError("contract_changed")


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
    if previous["controls"]["head"] != current["controls"]["head"] or previous["controls"]["refs"] != current["controls"]["refs"]:
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


def execute(store_path, action, input_path=None):
    store = Store(store_path)
    with store.locked(readonly=action == "resume"):
        events = store.load()
        contract, state, confirmed, baseline, checked, reviewed = validate_events(events)
        Store(store_path, contract["repo"])
        run_id = contract["run_id"]
        if state == "RUNNING":
            if action == "resume":
                return {"state": "BLOCKED", "last_confirmed": confirmed,
                        "reason": "interrupted_check", "next": "inspect_then_new_run", "run_id": run_id}
            raise RuntimeError("interrupted_check")
        current = snapshot.capture(contract["repo"], events[0]["payload"]["input_path"], contract["sources"])
        unchanged_authority(events[0], current)
        current_digest = snapshot.digest(current)
        valid_snapshot({"snapshot": current, "digest": current_digest}, contract)
        if action == "resume":
            reason = None
            permitted_current = False
            if baseline:
                try:
                    permitted_change(contract, baseline, current)
                    permitted_current = True
                    if checked and checked["digest"] != current_digest:
                        reason = "stale_check"
                except RuntimeError as error:
                    reason = str(error)
            if state == "BLOCKED":
                reason = events[-1]["payload"]["reason"]
            if state == "CHECKED" and contract["review"]["independence"] == "required" and reason is None:
                reason = "independence_unknown"
            next_action = {"DRAFT": "baseline", "BASELINED": "check", "CHECKED": "review",
                           "REVIEWED": "close", "CLOSED": "none", "BLOCKED": "inspect_then_check"}[state]
            if reason:
                if reason == "independence_unknown":
                    next_action = "trust_adapter_required"
                elif (state == "BLOCKED" and permitted_current and
                      reason in ("check_failed", "check_timeout", "check_output_limit")):
                    next_action = "inspect_then_check"
                else:
                    next_action = "inspect_then_new_run"
            return {"state": "BLOCKED" if reason else state, "last_confirmed": confirmed,
                    "reason": reason, "next": next_action, "run_id": run_id}
        if action == "baseline":
            if state != "DRAFT":
                raise RuntimeError("baseline_requires_draft")
            store.append(events, "BASELINED", {"snapshot": current, "digest": current_digest}, run_id)
            return {"state": "BASELINED", "snapshot": current_digest, "next": "check"}
        if not baseline:
            raise RuntimeError("missing_baseline")
        permitted_change(contract, baseline, current)
        if action == "check":
            if state == "CLOSED":
                raise RuntimeError("closed_requires_new_run")
            running = store.append(events, "RUNNING", {"snapshot": current, "digest": current_digest,
                                                        "started_ns": time.time_ns()}, run_id)
            events.append(running)
            journal = runner.run_check(snapshot.git_binary(), contract["repo"])
            after = snapshot.capture(contract["repo"], events[0]["payload"]["input_path"], contract["sources"])
            reason = ("check_timeout" if journal["timeout"] else "check_output_limit" if journal["truncated"]
                      else "check_failed" if journal["exit_code"] != 0 else
                      "inputs_changed_during_check" if after != current else None)
            kind = "BLOCKED" if reason else "CHECKED"
            store.append(events, kind, {"snapshot_digest": current_digest, "baseline_digest": baseline["digest"],
                                       "journal": journal, "reason": reason, "finished_ns": time.time_ns()}, run_id)
            return {"state": kind, "snapshot": current_digest, "reason": reason,
                    "next": "inspect_then_check" if reason else "review"}
        if not checked or state == "BLOCKED":
            raise RuntimeError("missing_check" if state != "BLOCKED" else events[-1]["payload"]["reason"])
        if checked["digest"] != current_digest:
            raise RuntimeError("stale_check")
        if action == "review":
            if state != "CHECKED":
                raise RuntimeError("review_requires_checked")
            if contract["review"]["independence"] == "required":
                raise RuntimeError("independence_unknown")
            review_path = snapshot.safe_path(input_path)
            if snapshot.path_within(review_path, contract["repo"]):
                raise RuntimeError("review_input_must_be_outside_repository")
            if snapshot.path_within(review_path, store.path):
                raise RuntimeError("review_input_must_be_outside_store")
            value = bounded_json(review_path)
            fields(value, ("schema", "run_id", "snapshot", "decision"))
            if type(value["schema"]) is not int or value["schema"] != 1 or value["run_id"] != run_id or value["snapshot"] != current_digest or value["decision"] != "approve":
                raise RuntimeError("invalid_operator_review")
            store.append(events, "REVIEWED", {"snapshot_digest": current_digest,
                                              "review_hash": snapshot.digest(value), "independence": "not_required"}, run_id)
            return {"state": "REVIEWED", "independence": "not_required", "next": "close"}
        if action == "close":
            if contract["review"]["independence"] == "required":
                raise RuntimeError("independence_unknown")
            if state != "REVIEWED" or not reviewed:
                raise RuntimeError("missing_review")
            store.append(events, "CLOSED", {"snapshot_digest": current_digest}, run_id)
            return {"state": "CLOSED", "claim": "staged-whitespace", "independence": "not_required"}
        raise RuntimeError("unknown_action")
