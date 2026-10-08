STATES = ("PROVEN", "NOT_REQUIRED", "UNKNOWN")

def independence(required):
    # First local slice has no identity/fresh-context provider.
    return "UNKNOWN" if required else "NOT_REQUIRED"
