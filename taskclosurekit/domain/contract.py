"""Pure strict contract parsing. Paths are declarations, never commands."""
from dataclasses import dataclass
import hashlib
import json
import re
from .authority import Authority
from .criterion import AcceptanceCriterion

SCHEMA = "taskclosurekit/v2"
CLAIM_TYPE = "configured-acceptance-satisfied"

def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

def fields(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise RuntimeError("unknown_or_missing_fields")

def identifier(value):
    if type(value) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", value):
        raise RuntimeError("invalid_identifier")
    return value

def path(value, absolute=False):
    if type(value) is not str or not value or len(value) > 1024 or "\\" in value or any(ord(x)<32 for x in value):
        raise RuntimeError("invalid_path")
    if absolute:
        if not value.startswith("/") or value == "/":
            raise RuntimeError("invalid_repository")
        parts = value[1:].split("/")
    else:
        parts = value.split("/")
        if value.startswith("/") or any(x.lower()==".git" for x in parts):
            raise RuntimeError("invalid_relative_path")
    if any(x in ("", ".", "..") or "*" in x or "?" in x for x in parts):
        raise RuntimeError("invalid_path")
    return value

def paths(value, allow_empty=False):
    if type(value) is not list or not (0 if allow_empty else 1) <= len(value) <= 100:
        raise RuntimeError("invalid_contract_paths")
    result = tuple(path(x) for x in value)
    if len(set(result)) != len(result):
        raise RuntimeError("duplicate_contract_paths")
    return result

@dataclass(frozen=True)
class TaskContract:
    id: str
    title: str
    repository: str
    authority: Authority
    sources: tuple
    acceptance: tuple
    review_required: bool
    review_independence: str
    claim_type: str
    digest: str

    def legacy_policy(self):
        return {"repo": self.repository, "mode": "Direct", "scope": list(self.authority.write),
                "sources": list(self.sources)}

def parse_contract(value):
    fields(value, ("schema", "task", "repository", "authority", "sources", "acceptance", "review", "claim", "closure"))
    if value["schema"] != SCHEMA:
        raise RuntimeError("unsupported_schema")
    fields(value["task"], ("id", "title"))
    task_id = identifier(value["task"]["id"])
    title = value["task"]["title"]
    if type(title) is not str or not 1 <= len(title) <= 256 or any(ord(x)<32 for x in title):
        raise RuntimeError("invalid_title")
    repository = path(value["repository"], absolute=True)
    a = value["authority"]
    fields(a, ("task", "read", "write", "execution"))
    fields(a["task"], ("issuer",))
    fields(a["execution"], ("presets",))
    fields(value["closure"], ("authority",))
    if a["task"]["issuer"] != "human" or value["closure"]["authority"] != "human":
        raise RuntimeError("unsupported_authority")
    read, write, sources = paths(a["read"]), paths(a["write"], True), paths(value["sources"])
    if any(not any(s==r or s.startswith(r+"/") for r in read) for s in sources):
        raise RuntimeError("source_outside_read_scope")
    presets = a["execution"]["presets"]
    if type(presets) is not list or not 1 <= len(presets) <= 32 or any(type(p) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", p) for p in presets) or len(set(presets)) != len(presets):
        raise RuntimeError("unknown_preset")
    criteria = value["acceptance"]
    if type(criteria) is not list or not 1 <= len(criteria) <= 100:
        raise RuntimeError("invalid_acceptance")
    parsed = []
    for c in criteria:
        fields(c, ("id", "required", "evidence"))
        identifier(c["id"])
        if type(c["required"]) is not bool or type(c["evidence"]) is not dict or len(c["evidence"]) != 1:
            raise RuntimeError("invalid_acceptance")
        composition = next(iter(c["evidence"]))
        if composition not in ("all_of", "any_of"):
            raise RuntimeError("unsupported_composition")
        refs = c["evidence"][composition]
        if type(refs) is not list or not 1 <= len(refs) <= 100 or any(type(p) is not str or p not in presets for p in refs) or len(set(refs))!=len(refs):
            raise RuntimeError("invalid_evidence_reference")
        parsed.append(AcceptanceCriterion(c["id"], c["required"], composition, tuple(refs)))
    if len({c.id for c in parsed}) != len(parsed) or not any(c.required for c in parsed):
        raise RuntimeError("invalid_acceptance")
    fields(value["review"], ("required", "independence"))
    review = value["review"]
    if type(review["required"]) is not bool or review["independence"] not in ("required", "not_required") or (not review["required"] and review["independence"] == "required"):
        raise RuntimeError("unsupported_review_contract")
    fields(value["claim"], ("type",))
    if value["claim"]["type"] != CLAIM_TYPE:
        raise RuntimeError("unsupported_claim")
    return TaskContract(task_id, title, repository, Authority("human", read, write, tuple(presets), "human"),
                        sources, tuple(parsed), review["required"], review["independence"], CLAIM_TYPE, identity(value))
