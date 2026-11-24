"""Authenticates task deliveries.

Production: Cloud Tasks attaches a Google-signed OIDC token for the tasks-invoker service account.
Local/test: the local dispatcher signs an HS256 token with a shared secret (refused elsewhere by
Settings validation).
"""

from dataclasses import dataclass
from typing import Any, Protocol

import jwt

from firra.settings import Settings

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"]
LOCAL_ISSUER = "firra-local-dispatcher"
LOCAL_AUDIENCE = "firra-worker-local"


class TaskAuthError(Exception):
    """Raised when a task request is not from our dispatcher."""


class SigningKeySource(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class TaskAuthenticator:
    settings: Settings
    jwks: SigningKeySource

    def verify(self, authorization: str | None) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise TaskAuthError("missing bearer token")
        token = authorization.removeprefix("Bearer ").strip()
        try:
            if self.settings.task_auth_mode == "oidc":
                self._verify_oidc(token)
            else:
                self._verify_local(token)
        except jwt.PyJWTError as exc:
            raise TaskAuthError(type(exc).__name__) from exc

    def _verify_oidc(self, token: str) -> None:
        key = self.jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=self.settings.task_oidc_audience,
            issuer=GOOGLE_ISSUERS,
            options={"require": ["exp", "iat", "iss", "aud", "email"]},
        )
        if claims.get("email") != self.settings.task_invoker_email:
            raise TaskAuthError("unexpected service account")
        if claims.get("email_verified") is not True:
            raise TaskAuthError("unverified service account email")

    def _verify_local(self, token: str) -> None:
        secret = self.settings.local_task_secret
        if secret is None:  # guarded by Settings validation
            raise TaskAuthError("local secret not configured")
        jwt.decode(
            token,
            secret.get_secret_value(),
            algorithms=["HS256"],
            audience=LOCAL_AUDIENCE,
            issuer=LOCAL_ISSUER,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )


def google_jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True, lifespan=3600, timeout=5)
