"""Delete everything: revoke provider tokens, forget secrets, empty every table (plan L8-03).

Best effort at providers (a dead token or no network never blocks the deletion), certain in the
database: every row of every table in schema ``public`` and the local owner go in one transaction.
Only counts are returned or logged. The Mac app then removes the data directory and its Keychain
items and quits.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import psycopg
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.connectors.slack import auth as slack_auth
from firra.db.local_wipe import empty_database
from firra.local.secrets import SecretStore
from firra.logging import get_logger

log = get_logger(__name__)
GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"


@dataclass(frozen=True)
class Wipe:
    """What deleting everything needs from the running engine."""

    secrets: SecretStore
    send_secret_delete: Callable[[str], None]
    http: httpx.Client


def _revoke(http: httpx.Client, name: str, value: str) -> bool | None:
    """True revoked, False failed, None when the slot holds no provider token."""
    try:
        if name.startswith("google_refresh_token:"):
            response = http.post(
                GOOGLE_REVOKE,
                content=urlencode({"token": value}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
            return response.status_code < 400
        if name.startswith("slack_user_token:"):
            slack_auth.revoke(http, value)
            return True
    except Exception:
        return False
    return None


def delete_everything(
    pool: ConnectionPool[psycopg.Connection[DictRow]], wipe: Wipe | None
) -> dict[str, Any]:
    revoked = failed = secrets = 0
    if wipe is not None:
        for name in wipe.secrets.names():
            value = wipe.secrets.get(name)
            outcome = _revoke(wipe.http, name, value) if value else None
            if outcome is True:
                revoked += 1
            elif outcome is False:
                failed += 1
            wipe.secrets.delete(name)
            wipe.send_secret_delete(name)
            secrets += 1
    deleted = empty_database(pool)
    log.info(
        "local.data.deleted",
        tables=len(deleted),
        rows=sum(deleted.values()),
        secrets=secrets,
        revoked=revoked,
        revoke_failed=failed,
    )
    return {"deleted": deleted, "secrets": secrets, "revoked": revoked, "revoke_failed": failed}
