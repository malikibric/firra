"""Workspace-scoped local Google accounts; refresh grants stay in SecretStore.

The only token reference persisted is an app-selected UUID in cursor. User-visible reads must
use ReadContext.caller(); engine maintenance supplies the one local owner's workspace explicitly.
"""

from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from firra.db.sweeps import SweepTarget
from firra.db.types import WorkspaceId


def visible(
    conn: Connection[DictRow], workspace_id: WorkspaceId, connection_id: UUID
) -> dict[str, Any] | None:
    return conn.execute(
        "select id, provider::text, status::text, account_label, backfill_months "
        "from public.connection where workspace_id = %(workspace_id)s "
        "and id = %(connection_id)s",
        {"workspace_id": workspace_id, "connection_id": connection_id},
    ).fetchone()


def connect_google(
    conn: Connection[DictRow],
    *,
    workspace_id: WorkspaceId,
    user_id: UUID,
    connection_id: UUID,
    account_label: str,
    backfill_months: int,
) -> list[UUID]:
    ids = []
    for provider in ("gmail", "google_calendar"):
        row = conn.execute(
            "insert into public.connection "
            "(id, workspace_id, provider, mode, owner_user_id, account_label, "
            "external_account_id, cursor, backfill_months) "
            "values (%(id)s, %(workspace_id)s, %(provider)s::public.connection_provider, "
            "'user', %(user_id)s, %(label)s, %(label)s, %(cursor)s, %(months)s) "
            "on conflict (workspace_id, provider, mode, external_account_id) "
            "where external_account_id is not null do update set status = 'active', "
            "last_error_class = null, account_label = excluded.account_label, "
            "cursor = connection.cursor || excluded.cursor, "
            "backfill_months = excluded.backfill_months "
            "returning id",
            {
                "id": connection_id if provider == "gmail" else uuid4(),
                "workspace_id": workspace_id,
                "provider": provider,
                "user_id": user_id,
                "label": account_label,
                "months": backfill_months,
                "cursor": Jsonb({"google_token_connection_id": str(connection_id)}),
            },
        ).fetchone()
        assert row is not None  # noqa: S101 - insert/upsert always returns its row
        ids.append(row["id"])
    return ids


def disconnect(conn: Connection[DictRow], *, workspace_id: WorkspaceId, slot_id: UUID) -> None:
    # Revoking one half of the account must also stop the other half sharing that grant.
    args = {"workspace_id": workspace_id, "slot_id": str(slot_id)}
    conn.execute(
        "update public.connection set status = 'revoked', last_error_class = 'disconnected' "
        "where workspace_id = %(workspace_id)s "
        "and (cursor->>'google_token_connection_id' = %(slot_id)s or id::text = %(slot_id)s)",
        args,
    )
    conn.execute(
        "update public.job set status = 'failed', error_class = 'connection_unavailable', "
        "finished_at = now() where workspace_id = %(workspace_id)s "
        "and status in ('queued', 'running') and connection_id in "
        "(select id from public.connection where workspace_id = %(workspace_id)s "
        "and (cursor->>'google_token_connection_id' = %(slot_id)s or id::text = %(slot_id)s))",
        args,
    )


def active(conn: Connection[DictRow], *, workspace_id: WorkspaceId) -> list[SweepTarget]:
    rows = conn.execute(
        "select id from public.connection where workspace_id = %(workspace_id)s "
        "and status = 'active' and mode = 'user' and provider in ('gmail', 'google_calendar')",
        {"workspace_id": workspace_id},
    ).fetchall()
    return [SweepTarget(workspace_id, row["id"]) for row in rows]


def slot_id(
    conn: Connection[DictRow], *, workspace_id: WorkspaceId, connection_id: UUID
) -> UUID | None:
    """Engine-only cursor metadata, after the route proves owner RLS visibility."""
    row = conn.execute(
        "select coalesce(cursor->>'google_token_connection_id', id::text) as slot "
        "from public.connection where workspace_id = %(workspace_id)s and id = %(connection_id)s",
        {"workspace_id": workspace_id, "connection_id": connection_id},
    ).fetchone()
    return None if row is None else UUID(row["slot"])


def enqueue_job(
    conn: Connection[DictRow],
    *,
    workspace_id: WorkspaceId,
    connection_id: UUID,
    job_type: str,
    key: str,
    payload: dict[str, Any],
) -> UUID:
    """Persist the connection FK (needed for disconnect and backfill concurrency limits).

    Re-consent retries failed backfill windows from their saved progress, like the cloud flow.
    Repeated successful POSTs retain succeeded jobs and never duplicate a window.
    """
    params = {
        "workspace_id": workspace_id,
        "connection_id": connection_id,
        "job_type": job_type,
        "key": key,
        "payload": Jsonb(payload),
    }
    conn.execute(
        "insert into public.job (workspace_id, connection_id, type, idempotency_key, payload) "
        "values (%(workspace_id)s, %(connection_id)s, %(job_type)s, %(key)s, %(payload)s) "
        "on conflict (workspace_id, idempotency_key) do nothing",
        params,
    )
    conn.execute(
        "update public.job set status = 'queued', attempts = 0, "
        "error_class = null, finished_at = null "
        "where workspace_id = %(workspace_id)s and connection_id = %(connection_id)s "
        "and idempotency_key = %(key)s and status = 'failed'",
        params,
    )
    row = conn.execute(
        "select id from public.job where workspace_id = %(workspace_id)s "
        "and connection_id = %(connection_id)s and idempotency_key = %(key)s",
        params,
    ).fetchone()
    assert row is not None  # noqa: S101 - unique workspace/key insert or existing job
    return row["id"]  # type: ignore[no-any-return]
