"""Canonical source/class pairs: serialized class strings grant no authority."""
SOURCE_CLASSES = {"agent-import": "agent_attested", "local-preset": "measured_local",
                  "ci-adapter": "measured_ci", "local-operator": "human_confirmed",
                  "repository-observer": "observed"}

def valid_source(source, trust_class):
    return SOURCE_CLASSES.get(source.id) == source.trust_class == trust_class
