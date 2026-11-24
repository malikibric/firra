"""Access tokens for Microsoft Graph.

- ``user`` mode (delegated): the stored refresh token (from the OAuth consent in apps/api) is
  redeemed at the user's tenant. Microsoft may return a new refresh token; callers must replace
  the stored one with it (the old one is not revoked immediately, but should be discarded).
- ``org_app`` mode (application permissions): client-credentials grant in the customer's tenant
  with ``https://graph.microsoft.com/.default``; the tenant admin granted consent and restricts
  mailboxes with application access policies / RBAC for Applications.

Docs checked: Microsoft identity platform v2 token endpoint (authorization code, refresh token and
client credentials grants; invalid_grant / interaction_required for revoked or expired grants).
"""

import json
from dataclasses import dataclass

import httpx

from firra.connectors.errors import TokenRevokedError
from firra.http import ProviderError, request_with_retry

LOGIN = "https://login.microsoftonline.com"
MAIL_SCOPE = "https://graph.microsoft.com/Mail.Read"
CALENDAR_SCOPE = "https://graph.microsoft.com/Calendars.Read"
APP_SCOPE = "https://graph.microsoft.com/.default"
# Errors that no retry can fix: consent revoked, password reset, account disabled, app removed.
# invalid_client is not here: it means our own app secret is wrong, not a customer revocation.
_REVOKED = frozenset(
    {"invalid_grant", "interaction_required", "consent_required", "unauthorized_client"}
)


@dataclass(frozen=True)
class StoredMicrosoftTokens:
    """Decrypted connection.token_ciphertext for Microsoft connections."""

    tenant_id: str
    refresh_token: str | None = None  # user mode only

    @classmethod
    def from_json(cls, text: str) -> "StoredMicrosoftTokens":
        data = json.loads(text)
        refresh = data.get("refresh_token")
        return cls(
            tenant_id=str(data["tenant_id"]), refresh_token=str(refresh) if refresh else None
        )

    def to_json(self) -> str:
        data = {"tenant_id": self.tenant_id}
        if self.refresh_token:
            data["refresh_token"] = self.refresh_token
        return json.dumps(data)


@dataclass(frozen=True)
class AccessGrant:
    access_token: str
    rotated_refresh_token: str | None


def _token_url(tenant_id: str) -> str:
    if not tenant_id.replace("-", "").isalnum():
        raise ValueError("invalid tenant id")
    return f"{LOGIN}/{tenant_id}/oauth2/v2.0/token"


def _redeem(client: httpx.Client, tenant_id: str, form: dict[str, str]) -> dict[str, object]:
    response = request_with_retry(client, "POST", _token_url(tenant_id), data=form)
    if response.status_code in (400, 401):
        error = response.json().get("error") if response.content else None
        if error in _REVOKED:
            raise TokenRevokedError(error)
    if response.status_code >= 400:
        raise ProviderError.from_response(response)
    body: dict[str, object] = response.json()
    return body


@dataclass
class MicrosoftUserTokens:
    client_id: str
    client_secret: str
    client: httpx.Client

    def access_token(self, stored: StoredMicrosoftTokens, scope: str) -> AccessGrant:
        if not stored.refresh_token:
            raise TokenRevokedError("missing_refresh_token")
        body = _redeem(
            self.client,
            stored.tenant_id,
            {
                "grant_type": "refresh_token",
                "refresh_token": stored.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": f"{scope} offline_access",
            },
        )
        rotated = body.get("refresh_token")
        return AccessGrant(
            access_token=str(body["access_token"]),
            rotated_refresh_token=str(rotated)
            if rotated and rotated != stored.refresh_token
            else None,
        )


@dataclass
class MicrosoftAppTokens:
    client_id: str
    client_secret: str
    client: httpx.Client

    def access_token(self, tenant_id: str) -> str:
        body = _redeem(
            self.client,
            tenant_id,
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": APP_SCOPE,
            },
        )
        return str(body["access_token"])
