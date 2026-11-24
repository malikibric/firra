"""Job state transitions. Every statement is scoped by (workspace_id, id)."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.types import JobId, WorkspaceId


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    workspace_id: WorkspaceId
    id: JobId
    type: str
    attempts: int
    max_attempts: int
    # Resume point and counters of a long job (continuations read it back).
    progress: dict[str, Any] = field(default_factory=dict)


class JobStore(Protocol):
    def claim(
        self, *, workspace_id: WorkspaceId, job_id: JobId, job_type: str
    ) -> ClaimedJob | None:
        """Marks a queued/running job as running and counts the attempt. None = nothing to do."""
        ...

    def succeed(
        self, *, workspace_id: WorkspaceId, job_id: JobId, progress: dict[str, Any]
    ) -> None: ...

    def fail(
        self, *, workspace_id: WorkspaceId, job_id: JobId, error_class: str, final: bool
    ) -> None: ...

    def update_progress(
        self, *, workspace_id: WorkspaceId, job_id: JobId, progress: dict[str, Any]
    ) -> None:
        """Merges a progress patch while the job runs (shown in the onboarding UI)."""
        ...

    def create(
        self,
        *,
        workspace_id: WorkspaceId,
        job_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
        connection_id: UUID | None = None,
    ) -> JobId | None:
        """Inserts a queued job; None when one with this idempotency key already exists."""
        ...

    def running_ahead(
        self, *, workspace_id: WorkspaceId, job_id: JobId, connection_id: UUID
    ) -> int:
        """Other running jobs of this type on the connection that started before this one."""
        ...

    def requeue(
        self, *, workspace_id: WorkspaceId, job_id: JobId, progress: dict[str, Any]
    ) -> None:
        """Saves the resume point and puts a running job back in the queue with fresh attempts."""
        ...


_CLAIM = """
update public.job
   set status = 'running', attempts = attempts + 1, started_at = coalesce(started_at, now())
 where workspace_id = %(workspace_id)s and id = %(job_id)s and type = %(job_type)s
   and status in ('queued', 'running')
returning workspace_id, id, type, attempts, max_attempts, progress
"""

_SUCCEED = """
update public.job
   set status = 'succeeded', progress = progress || %(progress)s, error_class = null,
       finished_at = now()
 where workspace_id = %(workspace_id)s and id = %(job_id)s and status = 'running'
"""

_FAIL = """
update public.job
   set status = case when %(final)s then 'failed' else 'queued' end::public.job_status,
       error_class = %(error_class)s,
       finished_at = case when %(final)s then now() end
 where workspace_id = %(workspace_id)s and id = %(job_id)s and status = 'running'
"""


_PROGRESS = """
update public.job
   set progress = progress || %(progress)s
 where workspace_id = %(workspace_id)s and id = %(job_id)s and status = 'running'
"""


_REQUEUE = """
update public.job
   set status = 'queued', attempts = 0, progress = progress || %(progress)s
 where workspace_id = %(workspace_id)s and id = %(job_id)s and status = 'running'
"""


# A running job that has not written progress for 10 minutes is presumed dead (instance lost).
_RUNNING_AHEAD = """
select count(*) as ahead
  from public.job other
  join public.job me
    on me.workspace_id = other.workspace_id and me.id = %(job_id)s
 where other.workspace_id = %(workspace_id)s
   and other.connection_id = %(connection_id)s
   and other.type = me.type
   and other.status = 'running'
   and other.updated_at > now() - interval '10 minutes'
   and (other.started_at, other.id) < (me.started_at, me.id)
"""


_CREATE = """
insert into public.job (workspace_id, type, idempotency_key, payload, connection_id)
values (%(workspace_id)s, %(job_type)s, %(idempotency_key)s, %(payload)s, %(connection_id)s)
on conflict (workspace_id, idempotency_key) do nothing
returning id
"""


# Only explicit 503 responses authorize local redelivery. Serialize its budget
# decision against claims so exhaustion cannot overwrite a running worker.
_LOCAL_DELIVERY_STATE = """
select status, attempts, max_attempts from public.job
 where workspace_id = %(workspace_id)s and id = %(job_id)s
 for update
"""

_FAIL_LOCAL_DELIVERY = """
update public.job
   set status = 'failed', finished_at = now(), error_class = 'local_dispatch_exhausted'
 where workspace_id = %(workspace_id)s and id = %(job_id)s and status = 'queued'
"""

LocalRetryDecision = Literal["retry", "done", "failed", "unowned", "missing"]


class PgJobStore:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def retry_local_delivery(
        self, *, workspace_id: WorkspaceId, job_id: JobId, deliveries: int
    ) -> LocalRetryDecision:
        """Observe ownership and budget after a definitive local HTTP 503.

        The delivery count also bounds failures before the worker claims a job.
        Never claim here: only the worker may increment execution attempts.
        """
        params = {"workspace_id": workspace_id, "job_id": job_id}
        with self._pool.connection() as conn:
            row = conn.execute(_LOCAL_DELIVERY_STATE, params).fetchone()
            if row is None:
                return "missing"
            if row["status"] == "succeeded":
                return "done"
            if row["status"] == "failed":
                return "failed"
            if row["status"] != "queued":
                return "unowned"
            if row["attempts"] >= row["max_attempts"] or deliveries >= row["max_attempts"]:
                conn.execute(_FAIL_LOCAL_DELIVERY, params)
                return "failed"
            return "retry"

    def claim(
        self, *, workspace_id: WorkspaceId, job_id: JobId, job_type: str
    ) -> ClaimedJob | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                _CLAIM, {"workspace_id": workspace_id, "job_id": job_id, "job_type": job_type}
            ).fetchone()
        if row is None:
            return None
        return ClaimedJob(
            workspace_id=WorkspaceId(row["workspace_id"]),
            id=JobId(row["id"]),
            type=row["type"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            progress=dict(row["progress"]),
        )

    def succeed(
        self, *, workspace_id: WorkspaceId, job_id: JobId, progress: dict[str, Any]
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                _SUCCEED,
                {"workspace_id": workspace_id, "job_id": job_id, "progress": Jsonb(progress)},
            )

    def fail(
        self, *, workspace_id: WorkspaceId, job_id: JobId, error_class: str, final: bool
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                _FAIL,
                {
                    "workspace_id": workspace_id,
                    "job_id": job_id,
                    "error_class": error_class,
                    "final": final,
                },
            )

    def update_progress(
        self, *, workspace_id: WorkspaceId, job_id: JobId, progress: dict[str, Any]
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                _PROGRESS,
                {"workspace_id": workspace_id, "job_id": job_id, "progress": Jsonb(progress)},
            )

    def running_ahead(
        self, *, workspace_id: WorkspaceId, job_id: JobId, connection_id: UUID
    ) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                _RUNNING_AHEAD,
                {"workspace_id": workspace_id, "job_id": job_id, "connection_id": connection_id},
            ).fetchone()
        return int(row["ahead"]) if row else 0

    def requeue(
        self, *, workspace_id: WorkspaceId, job_id: JobId, progress: dict[str, Any]
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                _REQUEUE,
                {"workspace_id": workspace_id, "job_id": job_id, "progress": Jsonb(progress)},
            )

    def create(
        self,
        *,
        workspace_id: WorkspaceId,
        job_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
        connection_id: UUID | None = None,
    ) -> JobId | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                _CREATE,
                {
                    "workspace_id": workspace_id,
                    "job_type": job_type,
                    "idempotency_key": idempotency_key,
                    "payload": Jsonb(payload),
                    "connection_id": connection_id,
                },
            ).fetchone()
        return None if row is None else JobId(row["id"])
