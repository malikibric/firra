"""In-memory secret slots handed over by the app on the control socket (contracts.md section 2).

The engine never writes a secret to disk, to a log or to an error message. Slot names are
validated by the ``HandshakeMessage`` contract; this store only keeps values and hides them from
``repr``.
"""

import threading


class SecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = threading.Lock()

    def set(self, name: str, value: str) -> None:
        with self._lock:
            self._values[name] = value

    def delete(self, name: str) -> None:
        with self._lock:
            self._values.pop(name, None)

    def get(self, name: str) -> str | None:
        with self._lock:
            return self._values.get(name)

    def require(self, name: str) -> str:
        value = self.get(name)
        if value is None:
            raise MissingSecretError(name)
        return value

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._values)

    def __repr__(self) -> str:
        return f"SecretStore({len(self._values)} slots)"


class MissingSecretError(Exception):
    """A slot was needed but the app has not sent it (carries the slot name, never a value)."""
