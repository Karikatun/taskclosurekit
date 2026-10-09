"""Private append-only HMAC journal. The host owner remains trusted."""

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat

from .snapshot import Budget, canonical, path_within, regular, safe_path


MAX_EVENTS = 64
EVENT_LIMIT = 16 * 1024 * 1024


def json_object(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RuntimeError("duplicate_fields")
            result[key] = value
        return result
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(RuntimeError("invalid_json")))
    except (UnicodeError, ValueError, RecursionError):
        raise RuntimeError("invalid_json") from None
    if not isinstance(value, dict):
        raise RuntimeError("object_required")
    return value


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise RuntimeError("unknown_or_missing_fields")


def bounded_json(path):
    return json_object(regular(safe_path(path), Budget(), 65536, True)[1])


class Store:
    def __init__(self, path, repo=None):
        self.path = safe_path(path)
        if repo is not None:
            repo = safe_path(repo)
            if path_within(self.path, repo) or path_within(repo, self.path):
                raise RuntimeError("store_must_be_outside_repository")

    @contextmanager
    def locked(self, create=False, readonly=False):
        import fcntl
        if create:
            # Initialization never reuses an existing directory or key.
            self.path.mkdir(mode=0o700)
        if not self.path.is_dir() or stat.S_IMODE(self.path.lstat().st_mode) != 0o700:
            raise RuntimeError("private_store_required")
        flags = os.O_RDONLY if readonly else os.O_RDWR
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        fd = os.open(str(self.path / "lock"), flags | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RuntimeError("invalid_store_lock")
            try:
                fcntl.flock(fd, (fcntl.LOCK_SH if readonly else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("store_busy") from None
            yield self
        finally:
            os.close(fd)

    def _write_atomic(self, name, data):
        temp = self.path / ("pending-" + os.urandom(8).hex())
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temp, self.path / name)
            directory = os.open(str(self.path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            # A leftover pending file is explicit interrupted persistence, never ignored.
            raise

    def initialize(self):
        self._write_atomic("key", os.urandom(32))

    def _key(self):
        identity, key = regular(self.path / "key", Budget(), 32, True)
        if identity["mode"] != 0o600 or len(key) != 32:
            raise RuntimeError("invalid_store_key")
        return key

    def load(self):
        key = self._key()
        with os.scandir(self.path) as scan:
            names = []
            for entry in scan:
                if not entry.is_file(follow_symlinks=False):
                    raise RuntimeError("invalid_store_entry")
                if entry.name not in ("key", "lock"):
                    if not re.fullmatch(r"[0-9]{4}\.json", entry.name):
                        raise RuntimeError("interrupted_or_unknown_store_entry")
                    names.append(entry.name)
        if len(names) > MAX_EVENTS:
            raise RuntimeError("store_limit")
        events = []
        previous = "0" * 64
        run_id = None
        budget = Budget()
        for seq, name in enumerate(sorted(names), 1):
            if name != "%04d.json" % seq:
                raise RuntimeError("store_sequence_mismatch")
            identity, data = regular(self.path / name, budget, EVENT_LIMIT, True)
            if identity["mode"] != 0o600:
                raise RuntimeError("private_evidence_required")
            event = json_object(data)
            fields(event, ("schema", "run_id", "seq", "previous", "kind", "payload", "signature"))
            signature = event["signature"]
            body = {key: value for key, value in event.items() if key != "signature"}
            if not isinstance(signature, str) or not hmac.compare_digest(
                    signature, hmac.new(key, canonical(body), hashlib.sha256).hexdigest()):
                raise RuntimeError("evidence_tampered")
            if type(event["schema"]) is not int or event["schema"] != 1 or type(event["seq"]) is not int or event["seq"] != seq or event["previous"] != previous:
                raise RuntimeError("store_binding_mismatch")
            if seq == 1:
                run_id = event["run_id"]
            if event["run_id"] != run_id or not isinstance(event["payload"], dict):
                raise RuntimeError("store_binding_mismatch")
            previous = hashlib.sha256(canonical(event)).hexdigest()
            events.append(event)
        if not events:
            raise RuntimeError("missing_contract")
        return events

    def append(self, events, kind, payload, run_id):
        if len(events) >= MAX_EVENTS:
            raise RuntimeError("store_limit")
        previous = hashlib.sha256(canonical(events[-1])).hexdigest() if events else "0" * 64
        event = {"schema": 1, "run_id": run_id, "seq": len(events) + 1,
                 "previous": previous, "kind": kind, "payload": payload}
        event["signature"] = hmac.new(self._key(), canonical(event), hashlib.sha256).hexdigest()
        data = canonical(event)
        if len(data) > EVENT_LIMIT:
            raise RuntimeError("store_limit")
        self._write_atomic("%04d.json" % event["seq"], data)
        return event
