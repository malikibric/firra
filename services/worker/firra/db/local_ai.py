"""Owner-scoped AI counts, processing dispatch and deterministic follow-up schedules."""

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.types import JobId, WorkspaceId


class LocalAiRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self.pool = pool

    def snapshot(self, *, workspace_id: WorkspaceId) -> dict[str, Any]:
        with self.pool.connection() as conn:
            counts = conn.execute(
                """select count(*) filter (where processing_state = 'pending')::int as pending,
                count(*) filter (where processing_state = 'done')::int as done
                from public.source_record where workspace_id = %(workspace_id)s""",
                {"workspace_id": workspace_id},
            ).fetchone()
            jobs = conn.execute(
                """select id, status::text, error_class, progress from public.job
                where workspace_id = %(workspace_id)s and type = 'process.records'
                order by (status in ('queued','running')) desc, created_at desc, id desc limit 1""",
                {"workspace_id": workspace_id},
            ).fetchone()
            rejected = conn.execute(
                """select progress -> 'dropped' as dropped from public.job
                where workspace_id = %(workspace_id)s and type = 'process.records'""",
                {"workspace_id": workspace_id},
            ).fetchall()
        return {
            "queue": {
                **dict(counts or {"pending": 0, "done": 0}),
                "rejected_items": sum(
                    sum(int(n) for n in (row["dropped"] or {}).values()) for row in rejected
                ),
            },
            "job": dict(jobs) if jobs else None,
        }

    def request(
        self, *, workspace_id: WorkspaceId, trigger: str, revision: int
    ) -> tuple[JobId, str] | None:
        key = f"process:{uuid4()}"
        with self.pool.connection() as conn, conn.transaction():
            # Serialize ingest and sweep at minute boundaries too, across threads/processes.
            conn.execute(
                "select pg_advisory_xact_lock(hashtextextended(%(workspace_id)s::text, 7))",
                {"workspace_id": workspace_id},
            )
            row = conn.execute(
                """insert into public.job
                (workspace_id, type, idempotency_key, payload, progress)
                select %(workspace_id)s, 'process.records', %(key)s, %(payload)s, %(progress)s
                where exists (select 1 from public.source_record r
                  where r.workspace_id = %(workspace_id)s and r.processing_state = 'pending')
                and not exists (select 1 from public.job j
                  where j.workspace_id = %(workspace_id)s and j.type = 'process.records'
                    and j.status in ('queued','running')) returning id""",
                {
                    "workspace_id": workspace_id,
                    "key": key,
                    "payload": Jsonb({"trigger": trigger}),
                    "progress": Jsonb({"ai_settings_revision": revision}),
                },
            ).fetchone()
        return (JobId(row["id"]), key) if row else None

    def routines(self, *, workspace_id: WorkspaceId, now: datetime) -> list[tuple[UUID, date]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select r.id, w.timezone from public.routine r join public.workspace w
                on w.id = r.workspace_id where r.workspace_id = %(workspace_id)s
                and r.enabled and r.kind in ('morning_scan','commitment_watch') order by r.id""",
                {"workspace_id": workspace_id},
            ).fetchall()
        return [(r["id"], now.astimezone(ZoneInfo(r["timezone"])).date()) for r in rows]
