"""Memory-only Desktop OAuth refresh and rotation for the local engine.

Docs checked: 2026-10-05, https://developers.google.com/identity/protocols/oauth2/native-app
(refresh grant, expiry and invalid_grant). No token ciphertext or authentication keyring is used.
"""

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import httpx

from firra.connectors.errors import TokenRevokedError
from firra.connectors.google.auth import GoogleAuth
from firra.connectors.google.credentials import TOKEN_URL
from firra.db.connections import ConnectionRow
from firra.db.types import WorkspaceId
from firra.http import ProviderError, request_with_retry
from firra.local.secrets import SecretStore
from firra.tasks.errors import PermanentTaskError


def token_slot(connection: ConnectionRow) -> str:
    # Gmail and Calendar share the app's one OAuth grant; cursor contains only its UUID.
    root = UUID(str(connection.cursor.get("google_token_connection_id", connection.id)))
    return f"google_refresh_token:{root}"


class LocalGoogleTokens(GoogleAuth):
    """GoogleAuth-compatible resolver; intentionally never constructs its cloud keyring."""

    def __init__(
        self,
        client: httpx.Client,
        secrets: SecretStore,
        *,
        client_id: str | None,
        client_secret: str | None,
        send_secret: Callable[[str, str], None],
        on_revoked: Callable[[ConnectionRow], None],
        # Wall clock: macOS stops the monotonic clock during sleep, which kept expired tokens alive.
        clock: Callable[[], float] = time.time,
        workspace_id: WorkspaceId | None = None,
        sync_interval_minutes: int = 5,
        config_file: Path | None = None,
    ) -> None:
        if sync_interval_minutes < 5:
            raise ValueError("sync interval must be at least five minutes")
        self.workspace_id = workspace_id
        self.sync_interval_minutes = sync_interval_minutes
        self._client = client
        self._secrets = secrets
        self._client_id = client_id
        self._client_secret = client_secret
        # App and engine share one Desktop client file; read on use, so adding it later works.
        self._config_file = config_file
        self._send_secret = send_secret
        self._on_revoked = on_revoked
        self._clock = clock
        self._cache: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "LocalGoogleTokens()"

    def _load_client(self) -> None:
        if self._client_id or self._config_file is None:
            return
        try:
            installed = json.loads(self._config_file.read_text())["installed"]
            client_id, secret = installed["client_id"], installed["client_secret"]
        except (OSError, ValueError, KeyError, TypeError):
            return
        if isinstance(client_id, str) and client_id and isinstance(secret, str) and secret:
            self._client_id, self._client_secret = client_id, secret

    def forget_access_tokens(self) -> None:
        """Drop cached access tokens (Google answered 401); the next call refreshes them."""
        with self._lock:
            self._cache.clear()

    def configured(self) -> bool:
        self._load_client()
        return bool(self._client_id)

    def access_token(self, connection: ConnectionRow, scopes: list[str]) -> str:
        if connection.mode != "user":
            raise PermanentTaskError("unsupported_connection_mode")
        self._load_client()
        if not self._client_id:
            raise PermanentTaskError("google_not_configured")
        name = token_slot(connection)
        with self._lock:
            refresh = self._secrets.get(name)
            if not refresh:
                self._cache.pop(name, None)
                self._on_revoked(connection)
                raise TokenRevokedError("token_revoked")
            cached = self._cache.get(name)
            if cached and cached[0] == refresh and self._clock() < cached[2]:
                return cached[1]
            form = {
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": self._client_id,
            }
            if self._client_secret:
                form["client_secret"] = self._client_secret
            try:
                response = request_with_retry(self._client, "POST", TOKEN_URL, data=form)
            except httpx.TransportError:
                # HTTP exceptions can include URLs or request headers; never propagate them.
                raise ProviderError(503) from None
            try:
                data = response.json()
            except ValueError:
                raise ProviderError(response.status_code) from None
            if not isinstance(data, dict):
                raise ProviderError(response.status_code)
            if response.status_code in (400, 401) and data.get("error") in (
                "invalid_grant",
                "unauthorized_client",
                "access_denied",
            ):
                self._cache.pop(name, None)
                self._on_revoked(connection)
                raise TokenRevokedError("token_revoked")
            if response.status_code >= 400:
                raise ProviderError(response.status_code)
            access, expiry = data.get("access_token"), data.get("expires_in")
            rotated = data.get("refresh_token", refresh)
            if (
                not isinstance(access, str)
                or not access
                or not isinstance(expiry, (int, float))
                or isinstance(expiry, bool)
                or expiry <= 0
                or not isinstance(rotated, str)
                or not rotated
            ):
                raise PermanentTaskError("invalid_token_response")
            # An app disconnect or a new consent while the exchange was in flight wins.
            if self._secrets.get(name) != refresh:
                self._cache.pop(name, None)
                raise TokenRevokedError("token_changed")
            if rotated != refresh:
                self._secrets.set(name, rotated)
                self._send_secret(name, rotated)
            self._cache[name] = (rotated, access, self._clock() + expiry - 60)
            return access
