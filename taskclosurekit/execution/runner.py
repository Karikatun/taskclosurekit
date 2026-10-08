from taskproof import runner, snapshot
from .presets import REGISTRY

def run(preset_id, repository):
    preset = REGISTRY.get(preset_id)
    # First registry member reuses the audited bounded runner unchanged.
    if preset.id != runner.PRESET or preset.argv != runner.CHECK_ARGS:
        raise RuntimeError("execution_or_contract_changed")
    return runner.run_check(snapshot.git_binary(), repository)
