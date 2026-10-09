"""Local byte integrity only; equivalent host authority can replace records/key/code."""
from .._primitives.store import Store, json_object
from .._primitives.snapshot import canonical

class LocalHmacStore(Store):
    def append(self, events, kind, payload, task_id):
        # Replay receives the exact JSON representation both before and after restart.
        payload = json_object(canonical(payload))
        return super().append(events, kind, payload, task_id)

    def read(self):
        return self.load()

    def verify(self):
        # Semantic v2 replay is separate and mandatory for application consumers.
        self.read()
        return True
