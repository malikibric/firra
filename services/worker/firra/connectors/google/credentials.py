"""Access tokens for Google APIs.

- ``user`` mode: the stored refresh token (from the OAuth consent in apps/api) is exchanged.
- ``org_delegated`` mode (domain-wide delegation): keyless. The worker service account signs the
  assertion through IAM Credentials ``signJwt``; the customer's admin authorized that account's
  client id for these scopes.

Verified against Google OAuth 2.0 docs (token endpoint, JWT bearer grant) at implementation time.
"""

import json
import time
from dataclasses import dataclass

import httpx

from firra.connectors.errors import TokenRevokedError
from firra.crypto.kek import MetadataTokenSource
from firra.http import ProviderError, request_with_retry

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - public endpoint, not a secret
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"


@dataclass(frozen=True)
class StoredGoogleTokens:
    """Decrypted connection.token_ciphertext for Google user connections."""

    refresh_token: str

    @classmethod
    def from_json(cls, text: str) -> "StoredGoogleTokens":
        data = json.loads(text)
        return cls(refresh_token=str(data["refresh_token"]))


def _exchange(client: httpx.Client, form: dict[str, str]) -> str:
    response = request_with_retry(client, "POST", TOKEN_URL, data=form)
    if response.status_code in (400, 401):
        error = response.json().get("error") if response.content else None
        if error in ("invalid_grant", "unauthorized_client", "access_denied"):
            raise TokenRevokedError(error)
    if response.status_code >= 400:
        raise ProviderError.from_response(response)
    return str(response.json()["access_token"])


@dataclass
class GoogleUserTokens:
    client_id: str
    client_secret: str
    client: httpx.Client

    def access_token(self, stored: StoredGoogleTokens) -> str:
        return _exchange(
            self.client,
            {
                "grant_type": "refresh_token",
                "refresh_token": stored.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )


@dataclass
class GoogleDelegatedTokens:
    service_account: str
    client: httpx.Client
    tokens: MetadataTokenSource

    def access_token(self, user_email: str, scopes: list[str]) -> str:
        now = int(time.time())
        claims = {
            "iss": self.service_account,
            "sub": user_email,
            "scope": " ".join(scopes),
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + 3000,
        }
        signed = request_with_retry(
            self.client,
            "POST",
            f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{self.service_account}:signJwt",
            json={"payload": json.dumps(claims)},
            headers={"Authorization": f"Bearer {self.tokens.token()}"},
        )
        if signed.status_code >= 400:
            raise ProviderError.from_response(signed)
        return _exchange(
            self.client,
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": str(signed.json()["signedJwt"]),
            },
        )
