def freshness(evidence, contract, current):
    if evidence.contract_digest != contract.digest or evidence.snapshot.authority != current.authority:
        return "STALE_AUTHORITY"
    if evidence.snapshot.execution != current.execution:
        return "STALE_ENVIRONMENT"
    # Repository, authority and execution cover every material check input.
    # The composite digest also binds staged review content in content-stage mode;
    # equivalent staging invalidates review without rerunning unchanged tests.
    if evidence.snapshot.repository != current.repository:
        return "STALE_INPUT"
    return "CURRENT"
