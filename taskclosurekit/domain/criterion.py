from dataclasses import dataclass

@dataclass(frozen=True)
class AcceptanceCriterion:
    id: str
    required: bool
    composition: str
    evidence: tuple
