"""Workspace- and mailbox-owner-scoped rehydration of existing records, never new ingest."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection, Cursor
from psycopg.rows import DictRow
from psycopg.sql import SQL
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.jobs import ClaimedJob
from firra.db.types import WorkspaceId
from firra.local.bodycrypt import write_body

_COLUMNS = SQL("r.id, r.external_id, r.occurred_at, r.content_hash")
# JSON lookup also supports pre-T61 databases without contributor.user_id.
_OWNER = SQL("""
 c.workspace_id = %(workspace_id)s and c.id = %(connection_id)s
 and c.mode = 'user' and c.status = 'active' and c.provider in ('gmail', 'outlook')
 and ((c.owner_user_id = %(user_id)s and exists (
        select 1 from public.membership m
         where m.workspace_id = c.workspace_id and m.user_id = %(user_id)s)) or
      (c.owner_user_id is null and exists (
        select 1 from public.contributor p
         where p.workspace_id = c.workspace_id and p.id = c.owner_contributor_id
           and p.status = 'active' and (to_jsonb(p)->>'user_id')::uuid = %(user_id)s)))
""")
_CANDIDATES = SQL("""
select {columns} from public.source_record r
 join public.connection c on c.workspace_id = r.workspace_id and c.id = r.connection_id
 join public.client cl on cl.workspace_id = r.workspace_id and cl.id = r.client_id
 where r.workspace_id = %(workspace_id)s and r.connection_id = %(connection_id)s
   -- Records ingested before delivery existed have no recipient yet: the connection's owner is it.
   and (r.vault_user_id = %(user_id)s or r.vault_user_id is null) and r.kind = 'email'
   and r.body_clean is null and r.body_purged_at is not null
   and r.policy_at_ingest = 'full' and cl.policy = 'full'
   and r.occurred_at >= %(until)s::timestamptz - make_interval(months => %(months)s)
   and r.occurred_at <= %(until)s::timestamptz and {owner}
""")
_SELECT = _CANDIDATES.format(columns=_COLUMNS, owner=_OWNER)
_PROGRESS = """
update public.job set progress = progress || %(progress)s
 where workspace_id = %(workspace_id)s and id = %(job_id)s
   and type = 'vault.rehydrate' and status = 'running'
"""


def _run(conn: Connection[DictRow], query: Any, params: dict[str, Any]) -> Cursor[DictRow]:
    """Executes a query composed with psycopg `SQL` from the module constants above; every value is
    a bound parameter."""
    # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query  # noqa: E501
    return conn.execute(query, params)


@dataclass(frozen=True, slots=True)
class VaultCandidate:
    id: UUID
    external_id: str
    occurred_at: datetime
    content_hash: bytes | None


class VaultRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def authorized(self, *, workspace_id: WorkspaceId, connection_id: UUID, user_id: UUID) -> bool:
        with self._pool.connection() as conn:
            return (
                _run(
                    conn,
                    SQL("""select c.id from public.connection c
                        where c.workspace_id = %(workspace_id)s and {owner}
                    """).format(owner=_OWNER),
                    {
                        "workspace_id": workspace_id,
                        "connection_id": connection_id,
                        "user_id": user_id,
                    },
                ).fetchone()
                is not None
            )

    def count(self, *, workspace_id: WorkspaceId, params: dict[str, Any]) -> int:
        with self._pool.connection() as conn:
            row = _run(
                conn,
                _CANDIDATES.format(columns=SQL("count(*) as total"), owner=_OWNER),
                {**params, "workspace_id": workspace_id},
            ).fetchone()
        return int(row["total"]) if row else 0

    def candidates(
        self, *, workspace_id: WorkspaceId, params: dict[str, Any], progress: dict[str, Any]
    ) -> list[VaultCandidate]:
        query = _SELECT + SQL("""
          and (%(cursor_at)s::timestamptz is null or (r.occurred_at, r.id) <
               (%(cursor_at)s::timestamptz, %(cursor_id)s::uuid))
          order by r.occurred_at desc, r.id desc limit 50
        """)
        with self._pool.connection() as conn:
            rows = _run(
                conn,
                query,
                {
                    **params,
                    "workspace_id": workspace_id,
                    "cursor_at": progress.get("cursor_at"),
                    "cursor_id": progress.get("cursor_id"),
                },
            ).fetchall()
        return [
            VaultCandidate(
                row["id"],
                row["external_id"],
                row["occurred_at"],
                bytes(row["content_hash"]) if row["content_hash"] is not None else None,
            )
            for row in rows
        ]

    def checkpoint(
        self,
        *,
        job: ClaimedJob,
        progress: dict[str, Any],
        params: dict[str, Any],
        record: VaultCandidate | None = None,
        body: str | None = None,
    ) -> None:
        """A restored body and its cursor/counters commit together; retries never double count.

        body_purged_at means "body was purged at least once", not that it is currently absent.
        Shared client/connection locks serialize policy stripping and revocation against restore.
        """
        with self._pool.connection() as conn:
            if record is not None and body is not None:
                query = SQL("""
                with eligible as ({candidates}
                  and r.id = %(record_id)s and r.content_hash = %(content_hash)s
                  for share of cl, c
                )
                update public.source_record r
                   set body_clean = %(body)s, rehydrated_at = now(), vault_delivered_at = null,
                       vault_user_id = coalesce(r.vault_user_id, %(user_id)s)
                  from eligible
                 where r.workspace_id = %(workspace_id)s and r.id = eligible.id
                   and r.body_clean is null and r.content_hash = %(content_hash)s
                returning r.id
                """).format(candidates=_SELECT)
                restored = _run(
                    conn,
                    query,
                    {
                        **params,
                        "workspace_id": job.workspace_id,
                        "record_id": record.id,
                        "content_hash": record.content_hash,
                        "body": write_body(self._pool, job.workspace_id, record.id, body),
                    },
                ).fetchone()
                key = "done" if restored is not None else "failed"
                progress[key] += 1
            result = _run(
                conn,
                _PROGRESS,
                {"workspace_id": job.workspace_id, "job_id": job.id, "progress": Jsonb(progress)},
            )
            if result.rowcount != 1:
                raise RuntimeError("rehydration job is not running")
