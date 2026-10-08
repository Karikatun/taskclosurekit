"""Trusted fixed configuration. Contracts select IDs; never executable text."""
from dataclasses import dataclass
from taskproof import runner
from ..domain.contract import identity

@dataclass(frozen=True)
class Preset:
    id: str
    executable: str
    argv: tuple
    cwd_rule: str
    environment_allowlist: tuple
    timeout: float
    output_limit: int
    permitted_writes: tuple

    @property
    def digest(self):
        return identity({"id":self.id,"executable":self.executable,"argv":self.argv,"cwd":self.cwd_rule,
                         "env":self.environment_allowlist,"timeout":self.timeout,"output":self.output_limit,
                         "writes":self.permitted_writes})

class PresetRegistry:
    def __init__(self):
        self._presets = {runner.PRESET: Preset(runner.PRESET, "validated-system-git", runner.CHECK_ARGS,
            "contract-repository", tuple(sorted(runner.environment().items())), runner.TIMEOUT,
            runner.OUTPUT_LIMIT, ())}

    def get(self, preset_id):
        if type(preset_id) is not str or preset_id not in self._presets:
            raise RuntimeError("unknown_preset")
        return self._presets[preset_id]

REGISTRY = PresetRegistry()
