from dataclasses import dataclass
from .evidence import EvidenceSource

@dataclass(frozen=True)
class AgentAssertion:
    id: str
    statement_digest: str
    source: EvidenceSource
    contract_digest: str
    snapshot_digest: str
    sequence: int
    recorded_ns: int
