from dataclasses import dataclass
from typing import Optional
from .claim import Claim

STATES = ("DRAFT", "AUTHORIZED", "BASELINED", "ACTIVE", "EVIDENCED", "REVIEWED", "CLAIMABLE", "CLOSED")

@dataclass(frozen=True)
class Evaluation:
    state: str
    decision: str
    reasons: tuple
    freshness: tuple
    evidence_set: str
    independence: str
    claim: Optional[Claim]
    next_action: str
