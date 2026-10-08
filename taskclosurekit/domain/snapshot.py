from dataclasses import dataclass

@dataclass(frozen=True)
class Snapshot:
    digest: str
    repository: str
    authority: str
    execution: str
    commit: str

@dataclass(frozen=True)
class CommitSnapshotBinding:
    repository: str
    commit: str
    snapshot_digest: str
