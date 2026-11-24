"""Resolves a Gmail/Calendar access token for a connection (user OAuth or delegation)."""

from dataclasses import dataclass

from firra.connectors.google.credentials import (
    GoogleDelegatedTokens,
    GoogleUserTokens,
    StoredGoogleTokens,
)
from firra.crypto.keyring import Keyring
from firra.db.connections import ConnectionRow
from firra.tasks.errors import PermanentTaskError


@dataclass
class GoogleAuth:
    user: GoogleUserTokens | None
    delegated: GoogleDelegatedTokens | None
    keyring: Keyring

    def access_token(self, connection: ConnectionRow, scopes: list[str]) -> str:
        if connection.mode == "user":
            if self.user is None or not connection.token_ciphertext:
                raise PermanentTaskError("google_not_configured")
            stored = StoredGoogleTokens.from_json(
                self.keyring.decrypt_token(
                    connection.workspace_id, connection.id, connection.token_ciphertext
                )
            )
            return self.user.access_token(stored)
        if connection.mode == "org_delegated":
            if self.delegated is None:
                raise PermanentTaskError("google_not_configured")
            return self.delegated.access_token(connection.account_label, scopes)
        raise PermanentTaskError("unsupported_connection_mode")
