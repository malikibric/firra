"""SQL of the client-scoped source layer (S2/S3). Reads synced records of ONE client.

Access is proven under the owner's row-level security first (``client``); only then are the
client's rows read through the engine pool, because bodies are hidden from the ``authenticated``
role (migration 22). Bodies, subjects and titles are decrypted in the engine, never in SQL.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId
from firra.local.bodycrypt import read_body, read_field

KINDS = {"email": "email", "slack": "chat", "calendar": "calendar_event"}
MAX_SCAN = 5000


def resolve_client(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, client: str
) -> DictRow | None:
    """The one non-excluded client that ``client`` (an id or an exact name) names, else None."""
    try:
        by_id = UUID(client)
    except ValueError:
        by_id = None
    rows = conn.execute(
        """select id, name, policy::text as policy from public.client
            where workspace_id = %(ws)s and policy <> 'excluded'
              and (id = %(id)s or lower(btrim(name)) = lower(btrim(%(name)s)))""",
        {"ws": workspace_id, "id": by_id, "name": client},
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


_RECORDS = """
select r.id, r.kind::text as kind, r.occurred_at, r.thread_key, r.body_clean, r.metadata,
       r.deep_link, r.direction::text as direction, p.display_name, p.side::text as side,
       exists (select 1 from public.evidence e
                where e.workspace_id = r.workspace_id and e.source_record_id = r.id) as has_evidence
  from public.source_record r
  join public.client c on c.workspace_id = r.workspace_id and c.id = r.client_id
  left join public.person p on p.workspace_id = r.workspace_id and p.id = r.author_person_id
 where r.workspace_id = %(ws)s and r.client_id = %(client)s and c.policy = 'full'
   and r.kind::text = any(%(kinds)s)
   and (%(since)s::timestamptz is null or r.occurred_at >= %(since)s)
   and (%(until)s::timestamptz is null or r.occurred_at <= %(until)s)
 order by r.occurred_at desc, r.id
 limit %(limit)s
"""


def _shape(pool: object, ws: WorkspaceId, row: DictRow) -> dict[str, Any]:
    metadata = row["metadata"] or {}
    subject = read_field(pool, ws, row["id"], "subject", metadata.get("subject"))
    title = read_field(pool, ws, row["id"], "title", metadata.get("title"))
    body = read_body(pool, ws, row["id"], row["body_clean"])
    name = row["display_name"] or metadata.get("author_name")
    return {
        "id": row["id"],
        "kind": row["kind"],
        "occurred_at": row["occurred_at"],
        "thread_key": row["thread_key"],
        "channel_id": metadata.get("channel_id"),
        "text": body if row["kind"] != "calendar_event" else (title or ""),
        "subject": subject if row["kind"] == "email" else None,
        "permalink": row["deep_link"],
        "author_name": name,
        "side": row["side"] or "unknown",
        "has_evidence": row["has_evidence"],
    }


def records(
    pool: ConnectionPool[psycopg.Connection[DictRow]],
    ws: WorkspaceId,
    client_id: UUID,
    sources: list[str],
    since: datetime | None,
    until: datetime | None,
) -> list[dict[str, Any]]:
    """Decrypted records of a Full client, newest first, at most ``MAX_SCAN``."""
    with pool.connection() as conn:
        rows = conn.execute(
            _RECORDS,
            {
                "ws": ws,
                "client": client_id,
                "kinds": [KINDS[s] for s in sources],
                "since": since,
                "until": until,
                "limit": MAX_SCAN,
            },
        ).fetchall()
    return [_shape(pool, ws, r) for r in rows]


def oldest_and_count(
    pool: ConnectionPool[psycopg.Connection[DictRow]],
    ws: WorkspaceId,
    client_id: UUID,
    sources: list[str],
    *,
    content_only: bool = True,
) -> tuple[datetime | None, datetime | None, int]:
    with pool.connection() as conn:
        row = conn.execute(
            """select min(occurred_at) as first, max(occurred_at) as last, count(*)::int as n
                 from public.source_record
                where workspace_id = %(ws)s and client_id = %(client)s
                  and kind::text = any(%(kinds)s)
                  and (not %(content)s or body_clean is not null or kind = 'calendar_event')""",
            {
                "ws": ws,
                "client": client_id,
                "kinds": [KINDS[s] for s in sources],
                "content": content_only,
            },
        ).fetchone()
    return (row["first"], row["last"], row["n"]) if row else (None, None, 0)


def record_of_ref(
    pool: ConnectionPool[psycopg.Connection[DictRow]], ws: WorkspaceId, ref: str
) -> tuple[UUID, UUID] | None:
    """(record id, client id) for a ``src_<hex>`` reference or an evidence id; None when unknown."""
    try:
        wanted = UUID(ref.removeprefix("src_")) if ref.startswith("src_") else UUID(ref)
    except ValueError:
        return None
    with pool.connection() as conn:
        row = conn.execute(
            """select r.id, r.client_id from public.source_record r
                where r.workspace_id = %(ws)s and r.client_id is not null
                  and (r.id = %(id)s or r.id = (select e.source_record_id from public.evidence e
                        where e.workspace_id = %(ws)s and e.id = %(id)s))
                limit 1""",
            {"ws": ws, "id": wanted},
        ).fetchone()
    return (row["id"], row["client_id"]) if row else None


def neighbours(
    pool: ConnectionPool[psycopg.Connection[DictRow]],
    ws: WorkspaceId,
    client_id: UUID,
    record_id: UUID,
    before: int,
    after: int,
) -> list[dict[str, Any]]:
    """The record and up to ``before``/``after`` neighbours of the same thread or channel."""
    with pool.connection() as conn:
        target = conn.execute(
            """select kind::text as kind, thread_key, metadata->>'channel_id' as channel_id
                 from public.source_record
                where workspace_id = %(ws)s and client_id = %(client)s and id = %(id)s""",
            {"ws": ws, "client": client_id, "id": record_id},
        ).fetchone()
        if target is None:
            return []
        same = (
            "r.kind::text = 'chat' and r.metadata->>'channel_id' is not distinct from"
            " %(channel)s and r.thread_key is not distinct from %(thread)s"
            if target["kind"] == "chat"
            else "r.kind::text = %(kind)s and r.thread_key is not distinct from %(thread)s"
        )
        rows = conn.execute(
            _RECORDS.replace("and r.kind::text = any(%(kinds)s)", f"and {same}").replace(
                "order by r.occurred_at desc, r.id", "order by r.occurred_at, r.id"
            ),
            {
                "ws": ws,
                "client": client_id,
                "kind": target["kind"],
                "thread": target["thread_key"],
                "channel": target["channel_id"],
                "since": None,
                "until": None,
                "limit": 200,
            },
        ).fetchall()
    shaped = [_shape(pool, ws, r) for r in rows]
    at = next((i for i, r in enumerate(shaped) if r["id"] == record_id), None)
    if at is None:
        return []
    return shaped[max(0, at - before) : at + after + 1]


def carrying_connections(
    pool: ConnectionPool[psycopg.Connection[DictRow]], ws: WorkspaceId, client_id: UUID | None
) -> list[DictRow]:
    """Connections (all, or those that carry records or channel mappings of one client)."""
    with pool.connection() as conn:
        return _carrying(conn, ws, client_id)


def _carrying(
    conn: psycopg.Connection[DictRow], ws: WorkspaceId, client_id: UUID | None
) -> list[DictRow]:
    return conn.execute(
        """select c.id, c.provider::text as provider, c.status::text as status,
                  c.last_synced_at
             from public.connection c
            where c.workspace_id = %(ws)s
              and (%(client)s::uuid is null
                   or exists (select 1 from public.source_record r
                               where r.workspace_id = c.workspace_id and r.connection_id = c.id
                                 and r.client_id = %(client)s)
                   or exists (select 1 from public.connection_scope s
                               where s.workspace_id = c.workspace_id and s.connection_id = c.id
                                 and s.client_id = %(client)s and s.include))
            order by c.created_at""",
        {"ws": ws, "client": client_id},
    ).fetchall()


def waiting_for_ai(
    pool: ConnectionPool[psycopg.Connection[DictRow]], ws: WorkspaceId, client_id: UUID | None
) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            """select count(*)::int as n from public.source_record
                where workspace_id = %(ws)s and processing_state = 'pending'
                  and (%(client)s::uuid is null or client_id = %(client)s)""",
            {"ws": ws, "client": client_id},
        ).fetchone()
    return int(row["n"]) if row else 0


def record_count(
    pool: ConnectionPool[psycopg.Connection[DictRow]], ws: WorkspaceId, client_id: UUID
) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            "select count(*)::int as n from public.source_record"
            " where workspace_id = %s and client_id = %s",
            (ws, client_id),
        ).fetchone()
    return int(row["n"]) if row else 0


def job_statuses(
    pool: ConnectionPool[psycopg.Connection[DictRow]], ws: WorkspaceId, job_ids: list[UUID]
) -> list[str]:
    with pool.connection() as conn:
        rows = conn.execute(
            "select status::text as status from public.job"
            " where workspace_id = %s and id = any(%s)",
            (ws, job_ids),
        ).fetchall()
    return [r["status"] for r in rows]
