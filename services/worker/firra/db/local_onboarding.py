"""SQL for onboarding: the latest suggestion run and the mail connections to read again."""

import hashlib
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

from firra.db.types import WorkspaceId


def latest_suggestion_job(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId
) -> DictRow | None:
    return conn.execute(
        "select status::text as status, progress, error_class from public.job"
        " where workspace_id = %s and type = 'onboarding.suggest'"
        " order by created_at desc limit 1",
        (workspace_id,),
    ).fetchone()


def gmail_connections(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "select id, backfill_months from public.connection"
        " where workspace_id = %s and provider = 'gmail' and status = 'active'"
        " order by created_at",
        (workspace_id,),
    ).fetchall()
    return [{"id": UUID(str(r["id"])), "months": int(r["backfill_months"])} for r in rows]


def supersede_queued_suggestions(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId
) -> None:
    """A new scan makes waiting ones pointless: the screen only shows the newest."""
    conn.execute(
        "update public.job set status = 'failed', finished_at = now(), error_class = 'superseded'"
        " where workspace_id = %s and type = 'onboarding.suggest' and status = 'queued'",
        (workspace_id,),
    )


def mapping_fingerprint(conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId) -> str:
    """Changes whenever the set of readable client domains changes (and only then)."""
    rows = conn.execute(
        "select d.domain, c.policy::text as policy from public.client_domain d"
        " join public.client c on c.workspace_id = d.workspace_id and c.id = d.client_id"
        " where d.workspace_id = %s order by d.domain, c.policy",
        (workspace_id,),
    ).fetchall()
    text = "\n".join(f"{r['domain']}:{r['policy']}" for r in rows)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def supersede_queued_rereads(conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId) -> None:
    """Waiting re-reads for an older mapping are replaced by the newest one (saving several
    clients in a row reads history about once, not once per client)."""
    conn.execute(
        "update public.job set status = 'failed', finished_at = now(), error_class = 'superseded'"
        " where workspace_id = %s and type = 'sync.backfill_chunk' and status = 'queued'"
        " and idempotency_key like 'reread:%%'",
        (workspace_id,),
    )
