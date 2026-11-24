"""Resolves a Microsoft Graph access token and user path for a connection."""

from dataclasses import dataclass
from urllib.parse import quote

from firra.connectors.microsoft.credentials import (
    MicrosoftAppTokens,
    MicrosoftUserTokens,
    StoredMicrosoftTokens,
)
from firra.crypto.keyring import Keyring
from firra.db.connections import ConnectionRepo, ConnectionRow
from firra.tasks.errors import PermanentTaskError


@dataclass(frozen=True)
class GraphAccess:
    access_token: str
    # "/me" for delegated connections, "/users/{id}" for application-permission connections.
    user_path: str


@dataclass
class MicrosoftAuth:
    user: MicrosoftUserTokens | None
    app: MicrosoftAppTokens | None
    keyring: Keyring
    connections: ConnectionRepo

    def _stored(self, connection: ConnectionRow) -> StoredMicrosoftTokens:
        if not connection.token_ciphertext:
            raise PermanentTaskError("microsoft_not_configured")
        return StoredMicrosoftTokens.from_json(
            self.keyring.decrypt_token(
                connection.workspace_id, connection.id, connection.token_ciphertext
            )
        )

    def access(self, connection: ConnectionRow, scope: str) -> GraphAccess:
        if connection.mode == "user":
            if self.user is None:
                raise PermanentTaskError("microsoft_not_configured")
            stored = self._stored(connection)
            grant = self.user.access_token(stored, scope)
            if grant.rotated_refresh_token:
                rotated = StoredMicrosoftTokens(stored.tenant_id, grant.rotated_refresh_token)
                self.connections.save_token(
                    workspace_id=connection.workspace_id,
                    connection_id=connection.id,
                    ciphertext=self.keyring.encrypt_token(
                        connection.workspace_id, connection.id, rotated.to_json()
                    ),
                )
            return GraphAccess(grant.access_token, "/me")
        if connection.mode == "org_app":
            if self.app is None or not connection.external_account_id:
                raise PermanentTaskError("microsoft_not_configured")
            stored = self._stored(connection)
            return GraphAccess(
                self.app.access_token(stored.tenant_id),
                f"/users/{quote(connection.external_account_id, safe='')}",
            )
        raise PermanentTaskError("unsupported_connection_mode")
