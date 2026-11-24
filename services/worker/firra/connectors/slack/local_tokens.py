"""Slack user tokens remain solely in the app's Keychain and engine SecretStore."""

from collections.abc import Callable
from uuid import UUID

from firra.connectors.errors import TokenRevokedError
from firra.db.connections import ConnectionRow
from firra.local.secrets import SecretStore


def token_slot(connection_id: UUID) -> str:
    return f"slack_user_token:{connection_id}"


class LocalSlackTokens:
    def __init__(
        self, secrets: SecretStore, *, on_revoked: Callable[[ConnectionRow], None] | None = None
    ) -> None:
        self._secrets = secrets
        self.on_revoked = on_revoked or (lambda _: None)

    def __repr__(self) -> str:
        return "LocalSlackTokens()"

    def access_token(self, connection_id: UUID) -> str:
        token = self._secrets.get(token_slot(connection_id))
        if not token or not token.startswith("xoxp-"):
            raise TokenRevokedError("token_revoked")
        return token
