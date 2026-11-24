import time
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from firra.tasks.auth import TaskAuthenticator, TaskAuthError
from tests.conftest import LOCAL_SECRET, local_settings
from tests.tokens import local_token

AUDIENCE = "https://worker.example.run.app"
INVOKER = "tasks-invoker@firra-staging.iam.gserviceaccount.com"
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class _Key:
    key: Any


class FakeJwks:
    def get_signing_key_from_jwt(self, _token: str) -> _Key:
        return _Key(_PRIVATE_KEY.public_key())


def google_token(**overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "email": INVOKER,
        "email_verified": True,
    }
    claims.update(overrides)
    return jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256")


def oidc_auth() -> TaskAuthenticator:
    settings = local_settings(
        env="staging",
        task_auth_mode="oidc",
        task_oidc_audience=AUDIENCE,
        task_invoker_email=INVOKER,
        local_task_secret=None,
    )
    return TaskAuthenticator(settings=settings, jwks=FakeJwks())


def test_oidc_accepts_cloud_tasks_token() -> None:
    oidc_auth().verify(f"Bearer {google_token()}")


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "https://other.run.app"},
        {"iss": "https://evil.example.com"},
        {"email": "someone@else.iam.gserviceaccount.com"},
        {"email_verified": False},
        {"exp": int(time.time()) - 10},
    ],
)
def test_oidc_rejects_wrong_claims(overrides: dict[str, Any]) -> None:
    with pytest.raises(TaskAuthError):
        oidc_auth().verify(f"Bearer {google_token(**overrides)}")


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer not-a-jwt"])
def test_rejects_missing_or_malformed_header(header: str | None) -> None:
    with pytest.raises(TaskAuthError):
        oidc_auth().verify(header)


def test_oidc_rejects_hs256_token_signed_with_local_secret() -> None:
    with pytest.raises(TaskAuthError):
        oidc_auth().verify(f"Bearer {local_token(LOCAL_SECRET)}")


def test_local_mode() -> None:
    auth = TaskAuthenticator(settings=local_settings(), jwks=FakeJwks())
    auth.verify(f"Bearer {local_token(LOCAL_SECRET)}")
    with pytest.raises(TaskAuthError):
        auth.verify(f"Bearer {local_token('x' * 40)}")
    with pytest.raises(TaskAuthError):
        auth.verify(f"Bearer {local_token(LOCAL_SECRET, aud='other')}")
