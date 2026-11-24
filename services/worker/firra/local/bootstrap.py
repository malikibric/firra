"""First start: the one local owner and workspace (docs/plans/local-v1.md L1-03)."""

from pathlib import Path

import psycopg

from firra.db.local_identity import LocalIdentity, create_owner, find_owner

DEFAULT_WORKSPACE_NAME = "My firm"


def local_timezone(localtime: Path = Path("/etc/localtime")) -> str:
    """The Mac's IANA time zone name (the target of /etc/localtime), UTC when it cannot be read."""
    try:
        target = str(localtime.readlink())
    except OSError:
        return "UTC"
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else "UTC"


def ensure_local_owner(conninfo: str, *, timezone: str | None = None) -> LocalIdentity:
    """The owner and workspace, created on the first call. Safe to call at every start."""
    with psycopg.connect(conninfo) as conn:
        existing = find_owner(conn)
        if existing is not None:
            return existing
        identity = create_owner(
            conn,
            workspace_name=DEFAULT_WORKSPACE_NAME,
            timezone=timezone or local_timezone(),
        )
        conn.commit()
        return identity
