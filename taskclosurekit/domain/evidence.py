from dataclasses import dataclass
from .snapshot import Snapshot

@dataclass(frozen=True)
class EvidenceSource:
    id: str
    trust_class: str

@dataclass(frozen=True)
class Evidence:
    id: str
    type: str
    trust_class: str
    source: EvidenceSource
    contract_digest: str
    snapshot: Snapshot
    criteria: tuple
    result: str
    sequence: int
    recorded_ns: int
