def freshness(evidence, contract, current):
    if evidence.contract_digest != contract.digest or evidence.snapshot.authority != current.authority:
        return "STALE_AUTHORITY"
    if evidence.snapshot.execution != current.execution:
        return "STALE_ENVIRONMENT"
    if evidence.snapshot.repository != current.repository or evidence.snapshot.digest != current.digest:
        return "STALE_INPUT"
    return "CURRENT"
