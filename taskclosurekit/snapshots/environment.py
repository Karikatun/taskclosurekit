from ..domain.contract import identity

def execution_identity(raw_snapshot):
    return identity(raw_snapshot["execution"])
