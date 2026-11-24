"""Local delivery persistence; the startup scan belongs to the sole engine runner."""

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.types import cross_tenant_maintenance
from firra_contracts import TaskEnvelope

SETUP_ERRORS = frozenset({"google_not_configured"})


class LocalJobs:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self.pool = pool

    def ensure(self, envelope: TaskEnvelope) -> TaskEnvelope:
        params = {
            "workspace_id": envelope.workspace_id,
            "id": envelope.job_id,
            "key": envelope.idempotency_key,
            "type": envelope.type,
            "payload": Jsonb(envelope.payload),
        }
        with self.pool.connection() as conn:
            # Callers may already have created this job; continuations retain its id.
            row = conn.execute(
                """select id, type, payload, idempotency_key from public.job
                where workspace_id = %(workspace_id)s and id = %(id)s""",
                params,
            ).fetchone()
            if row is None:
                conn.execute(
                    """insert into public.job (workspace_id, id, type, idempotency_key, payload)
                    values (%(workspace_id)s, %(id)s, %(type)s, %(key)s, %(payload)s)
                    on conflict (workspace_id, idempotency_key) do nothing""",
                    params,
                )
                row = conn.execute(
                    """select id, type, payload, idempotency_key from public.job
                    where workspace_id = %(workspace_id)s and idempotency_key = %(key)s""",
                    params,
                ).fetchone()
        if row is None:
            raise RuntimeError("local job unavailable")
        return envelope.model_copy(
            update={"job_id": row["id"], "type": row["type"], "payload": row["payload"]}
        )

    @cross_tenant_maintenance("sole local executor recovers pending jobs across all workspaces")
    def recover(self, non_resumable: frozenset[str]) -> list[TaskEnvelope]:
        with self.pool.connection() as conn:
            # Jobs that failed only because setup was missing (the Google client file) run again
            # at the next start; they are idempotent and fail again cheaply if it is still missing.
            conn.execute(
                """update public.job set status = 'queued', attempts = 0, error_class = null,
                finished_at = null where status = 'failed'
                and error_class = any(%(setup)s) and type <> 'onboarding.suggest'""",
                {"setup": list(SETUP_ERRORS)},
            )
            # Only the newest client-suggestion scan per workspace matters to the onboarding screen.
            conn.execute(
                """update public.job j set status = 'failed', finished_at = now(),
                error_class = 'superseded'
                where j.type = 'onboarding.suggest' and j.status in ('queued', 'running')
                and exists (select 1 from public.job n where n.workspace_id = j.workspace_id
                  and n.type = 'onboarding.suggest' and n.created_at > j.created_at)"""
            )
            rows = conn.execute(
                """select workspace_id, id, type, idempotency_key, payload from public.job
                where status in ('queued', 'running') order by created_at, id"""
            ).fetchall()
        pending = []
        for row in rows:
            if row["type"] in non_resumable:
                self.abandon(row["workspace_id"], row["id"])
            else:
                pending.append(
                    TaskEnvelope(
                        workspace_id=row["workspace_id"],
                        job_id=row["id"],
                        type=row["type"],
                        idempotency_key=row["idempotency_key"],
                        payload=row["payload"],
                    )
                )
        return pending

    def abandon(self, workspace_id: object, job_id: object) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """update public.job set status = 'failed', finished_at = now(),
                error_class = 'abandoned_on_restart'
                where workspace_id = %(workspace_id)s and id = %(id)s
                and status in ('queued', 'running')""",
                {"workspace_id": workspace_id, "id": job_id},
            )
