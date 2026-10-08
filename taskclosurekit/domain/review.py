from dataclasses import dataclass
from .evidence import EvidenceSource

@dataclass(frozen=True)
class Review:
    contract_digest: str
    snapshot_digest: str
    evidence_set: str
    verdict: str
    source: EvidenceSource
    independence: str
    sequence: int
