from dataclasses import dataclass

@dataclass(frozen=True)
class Authority:
    task_issuer: str
    read: tuple
    write: tuple
    presets: tuple
    closure_issuer: str
