"""What the scheduled sweep picks up: connections to sync, stuck jobs (explicitly cross-tenant)."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId, cross_tenant_maintenance

SWEEP_PROVIDERS = ("gmail", "google_calendar")


@dataclass(frozen=True, slots=True)
class SweepTarget:
    workspace_id: WorkspaceId
    connection_id: UUID


@cross_tenant_maintenance("incremental sync sweep covers every active connection")
def active_connections(pool: ConnectionPool[Connection[DictRow]]) -> list[SweepTarget]:
    query = """
    select workspace_id, id from public.connection
     where status = 'active'
       and provider::text = any(%(providers)s)
       and (token_ciphertext is not null or mode = 'org_delegated')
    """
    with pool.connection() as conn:
        rows = conn.execute(query, {"providers": list(SWEEP_PROVIDERS)}).fetchall()
    return [SweepTarget(WorkspaceId(r["workspace_id"]), r["id"]) for r in rows]


# Long jobs whose task can be lost (a delivery that kept failing is dropped by Cloud Tasks).
RESUMABLE_TYPES = ("sync.backfill_chunk", "ingest.upload", "process.records", "routines.run")


@dataclass(frozen=True, slots=True)
class StuckJob:
    workspace_id: WorkspaceId
    id: UUID
    type: str
    idempotency_key: str
    payload: dict[str, Any]


@cross_tenant_maintenance("sweep re-dispatches long jobs whose task was lost")
def claim_stuck_jobs(
    pool: ConnectionPool[Connection[DictRow]], *, idle_minutes: int = 30
) -> list[StuckJob]:
    """Unfinished long jobs untouched for ``idle_minutes``; marked touched so the next sweep waits.

    A live job writes progress every page and a waiting one is re-queued every few minutes, so a
    job idle this long has no task left that will run it.
    """
    query = """
    update public.job set updated_at = now()
     where status in ('queued', 'running')
       and type = any(%(types)s)
       and updated_at < now() - make_interval(mins => %(idle)s)
    returning workspace_id, id, type, idempotency_key, payload
    """
    with pool.connection() as conn:
        rows = conn.execute(
            query, {"types": list(RESUMABLE_TYPES), "idle": idle_minutes}
        ).fetchall()
    return [
        StuckJob(
            WorkspaceId(r["workspace_id"]), r["id"], r["type"], r["idempotency_key"], r["payload"]
        )
        for r in rows
    ]
