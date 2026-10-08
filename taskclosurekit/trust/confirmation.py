"""Strict trusted-host operator receipt schema; booleans must be JSON booleans."""

FIELDS = {"source", "binding", "host_operator_assumed", "identity_verified"}


def validate_confirmation(value, binding):
    if (type(value) is not dict or set(value) != FIELDS or
            type(value["source"]) is not str or value["source"] != "local-operator" or
            type(value["binding"]) is not str or value["binding"] != binding or
            value["host_operator_assumed"] is not True or
            value["identity_verified"] is not False):
        raise RuntimeError("invalid_authority_confirmation")
