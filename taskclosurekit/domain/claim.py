from dataclasses import dataclass

LIMITATIONS = ("Only configured required acceptance criteria are satisfied for these exact bindings.",
    "Does not prove universal correctness, absence of bugs or general security.",
    "Does not certify production readiness or authorize deployment.",
    "Local HMAC integrity assumes a trusted host; no hostile-agent sandbox or proven human identity.")

@dataclass(frozen=True)
class Claim:
    type: str
    contract_digest: str
    snapshot_digest: str
    evidence_set: str
    criteria: tuple
    limitations: tuple = LIMITATIONS

@dataclass(frozen=True)
class Closure:
    claim: Claim
    authority_source: str
    action_binding: str
    sequence: int
    identity_verified: bool = False
