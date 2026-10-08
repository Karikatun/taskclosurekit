"""Explicit trusted-host operator assumption, not an identity provider."""
import sys

class LocalOperator:
    def confirm(self, operation, binding):
        if not sys.stdin.isatty():
            raise RuntimeError("human_confirmation_unavailable")
        # Prompt contains only protocol operation and digest; never imported task text.
        print("Local operator assumption (identity not verified). Confirm " + operation +
              " by typing exactly: " + binding, file=sys.stderr, flush=True)
        answer = sys.stdin.readline(66)
        if answer != binding + "\n":
            raise RuntimeError("human_confirmation_rejected")
        return {"source":"local-operator", "binding":binding,
                "host_operator_assumed":True, "identity_verified":False}
