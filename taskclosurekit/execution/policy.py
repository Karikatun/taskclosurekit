"""Finite execution ceilings supplied by separately authorized preset configuration."""
from dataclasses import asdict, dataclass
import math
import sys
from ..domain.contract import fields

POLICY_SCHEMA = "taskclosurekit/execution-policy/v1"


@dataclass(frozen=True)
class ExecutionPolicy:
    timeout_limit: float = 300.0
    output_limit: int = 1024 * 1024
    cpu_seconds: int = 300
    file_bytes: int = 8 * 1024 * 1024
    open_files: int = 64
    address_space_bytes: int = 512 * 1024 * 1024
    diagnostic_limit: int = 4096
    require_complete_diagnostics: bool = False

    def definition(self):
        return {"schema": POLICY_SCHEMA, **asdict(self)}

    def profile(self, writable):
        return {"cpu_seconds": self.cpu_seconds, "file_bytes": self.file_bytes if writable else 0,
                "open_files": self.open_files, "core_bytes": 0,
                "address_space_bytes": self.address_space_bytes if sys.platform.startswith("linux") else None,
                "platform": sys.platform}


CEILINGS = {"timeout_limit": 86400.0, "output_limit": 64 * 1024 * 1024,
            "cpu_seconds": 86400, "file_bytes": 1024 * 1024 * 1024,
            "open_files": 4096, "address_space_bytes": 64 * 1024 * 1024 * 1024,
            "diagnostic_limit": 65536}


def parse_policy(value):
    if type(value) is not dict or value.get("schema") != POLICY_SCHEMA or set(value) - {"schema", *CEILINGS, "require_complete_diagnostics"}:
        raise RuntimeError("invalid_execution_policy")
    merged = ExecutionPolicy().definition()
    merged.update(value)
    for name, ceiling in CEILINGS.items():
        number = merged[name]
        expected = (int, float) if name == "timeout_limit" else (int,)
        if type(number) not in expected or not math.isfinite(number) or not 0 < number <= ceiling:
            raise RuntimeError("invalid_execution_policy")
    if type(merged["require_complete_diagnostics"]) is not bool or merged["open_files"] < 16:
        raise RuntimeError("invalid_execution_policy")
    return ExecutionPolicy(**{k: v for k, v in merged.items() if k != "schema"})


def validate_policy_record(value):
    fields(value, ("schema", *CEILINGS, "require_complete_diagnostics"))
    return parse_policy(value)
