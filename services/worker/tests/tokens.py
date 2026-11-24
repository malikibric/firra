"""Helpers that mint task tokens the way the local dispatcher and Cloud Tasks do."""

import time
from typing import Any

import jwt

from firra.tasks.auth import LOCAL_AUDIENCE, LOCAL_ISSUER


def local_token(secret: str, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": LOCAL_ISSUER,
        "aud": LOCAL_AUDIENCE,
        "iat": now,
        "exp": now + 60,
    }
    claims.update(overrides)
    return jwt.encode(claims, secret, algorithm="HS256")
