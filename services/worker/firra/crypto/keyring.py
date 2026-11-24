"""Per-workspace data keys: created on first use, stored wrapped, cached unwrapped per process."""

from dataclasses import dataclass, field

from firra.crypto import envelope
from firra.crypto.kek import Kek
from firra.db.keys import WorkspaceKeyStore
from firra.db.types import WorkspaceId


@dataclass
class Keyring:
    kek: Kek
    store: WorkspaceKeyStore
    _cache: dict[WorkspaceId, bytes] = field(default_factory=dict, init=False, repr=False)

    def dek(self, workspace_id: WorkspaceId) -> bytes:
        cached = self._cache.get(workspace_id)
        if cached is not None:
            return cached
        wrapped = self.store.get(workspace_id=workspace_id)
        if wrapped is None:
            # Insert-if-absent: concurrent creators converge on the first stored key.
            wrapped = self.store.insert_if_absent(
                workspace_id=workspace_id,
                wrapped_dek=self.kek.wrap(envelope.new_dek()),
                kek_name=self.kek.name,
            )
        dek = self.kek.unwrap(wrapped)
        self._cache[workspace_id] = dek
        return dek

    def encrypt_token(self, workspace_id: WorkspaceId, connection_id: object, token: str) -> str:
        return envelope.encrypt(
            self.dek(workspace_id), token, envelope.token_aad(workspace_id, connection_id)
        )

    def decrypt_token(self, workspace_id: WorkspaceId, connection_id: object, blob: str) -> str:
        return envelope.decrypt(
            self.dek(workspace_id), blob, envelope.token_aad(workspace_id, connection_id)
        )
