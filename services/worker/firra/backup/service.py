"""Export and restore of the owner's non-recoverable state (plan L8-02).

What is in the file: clients with policies and contract facts, domains, services, routine
settings, outcomes, plus channel mappings and feedback by stable keys (``pending_*``, applied by
the sync and the processing after the first resync). Mail, chat bodies, extractions, signals and
assessments are not exported: they come back from the providers and the owner's AI.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

from firra.backup import crypto
from firra.db import local_backup
from firra.db.types import WorkspaceId

FORMAT = 1


class RestoreError(Exception):
    """The file is valid but cannot be restored here (not empty, wrong format)."""


def _plain(value: Any) -> Any:
    if isinstance(value, UUID | date | datetime):
        return value.isoformat() if not isinstance(value, UUID) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def export_backup(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, phrase: str, *, now: datetime
) -> bytes:
    state = local_backup.read_state(conn, workspace_id)
    payload = json.loads(
        json.dumps({"format": FORMAT, "created_at": now.astimezone(UTC), **state}, default=_plain)
    )
    return crypto.encrypt(payload, phrase)


def restore_backup(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, blob: bytes, phrase: str
) -> dict[str, Any]:
    """Restores into an empty workspace; returns the pending lists for the resync to apply."""
    state = crypto.decrypt(blob, phrase)
    if state.get("format") != FORMAT:
        raise RestoreError("unsupported format")
    if not local_backup.workspace_is_empty(conn, workspace_id):
        raise RestoreError("workspace is not empty")
    with conn.transaction():
        ids = local_backup.restore_state(conn, workspace_id, state)
    remap = {str(old): str(new) for old, new in ids.items()}
    return {
        "clients": len(ids),
        "pending_scopes": [
            {**s, "client_id": remap.get(str(s["client_id"]))} for s in state["scopes"]
        ],
        "pending_feedback": [
            {**f, "client_id": remap[str(f["client_id"])]}
            for f in state["feedback"]
            if str(f["client_id"]) in remap
        ],
    }
