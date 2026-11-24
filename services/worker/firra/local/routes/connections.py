"""Local Google account setup after the app hands its grant over the control socket.

No refresh/access token is accepted or returned by HTTP. Read visibility is checked under the
owner's RLS before engine writes. Connection rows contain only the shared secret-slot UUID.
"""

from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Response
from pydantic import BaseModel, ConfigDict, Field

from firra.db import local_connections
from firra.db.types import WorkspaceId
from firra.local.readctx import ReadContext
from firra.local.secrets import SecretStore
from firra.local.server import ApiError
from firra.tasks.context import TaskContext
from firra.tasks.handlers.sync_common import month_windows
from firra_contracts import TaskEnvelope


class GoogleConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_label: str = Field(min_length=3, max_length=320)
    backfill_months: Literal[3, 6, 12] = 3
    connection_id: UUID


def build_router(
    ctx: ReadContext,
    *,
    secrets: SecretStore,
    tasks: TaskContext,
    send_secret_delete: Callable[[str], None],
) -> APIRouter:
    router = APIRouter(prefix="/local/connections")
    workspace_id = WorkspaceId(ctx.identity.workspace_id)

    def persist(jobs: list[tuple[UUID, str, str, dict[str, Any]]]) -> list[TaskEnvelope]:
        # One connection for all jobs, and dispatch only after the response: the executor starts
        # on the first job and competes for the pool, which made the connect call take about 5 s.
        # Persisted jobs are not lost if the engine stops first (the executor recovers them).
        with ctx.pool.connection() as conn:
            envelopes = [
                TaskEnvelope(
                    workspace_id=workspace_id,
                    job_id=local_connections.enqueue_job(
                        conn,
                        workspace_id=workspace_id,
                        connection_id=connection_id,
                        job_type=job_type,
                        key=key,
                        payload=payload,
                    ),
                    idempotency_key=key,
                    type=job_type,
                    payload=payload,
                )
                for connection_id, job_type, key, payload in jobs
            ]
        return envelopes

    def dispatch(envelopes: list[TaskEnvelope]) -> None:
        for envelope in envelopes:
            tasks.queue.enqueue(envelope)

    def find(connection_id: UUID) -> dict[str, Any]:
        with ctx.caller() as conn:
            row = local_connections.visible(conn, workspace_id, connection_id)
        if row is None:
            raise ApiError(404, "not_found")
        return row

    @router.post("/google")
    def connect(body: GoogleConnectionRequest, background: BackgroundTasks) -> dict[str, str]:
        label = body.account_label.strip().lower()
        if label.count("@") != 1 or any(c.isspace() for c in label):
            raise ApiError(422, "invalid_request")
        name = f"google_refresh_token:{body.connection_id}"
        if not secrets.get(name):
            raise ApiError(409, "missing_secret")
        google = tasks.google
        if hasattr(google, "configured") and not google.configured():
            # Refuse before creating jobs that could only fail (no Desktop client file yet).
            raise ApiError(
                409,
                "google_not_configured",
                "Firra cannot find google-oauth-client.json in its Application Support folder.",
            )
        with ctx.pool.connection() as conn:
            ids = local_connections.connect_google(
                conn,
                workspace_id=workspace_id,
                user_id=ctx.identity.user_id,
                connection_id=body.connection_id,
                account_label=label,
                backfill_months=body.backfill_months,
            )
        now = ctx.clock()
        jobs: list[tuple[UUID, str, str, dict[str, Any]]] = []
        for connection_id in ids:
            jobs.append(
                (
                    connection_id,
                    "sync.incremental",
                    f"incremental-init:{connection_id}:{body.connection_id}",
                    {"connection_id": str(connection_id)},
                )
            )
            for start, end in month_windows(now, body.backfill_months):
                jobs.append(
                    (
                        connection_id,
                        "sync.backfill_chunk",
                        f"backfill:{connection_id}:{start:%Y-%m}:{body.connection_id}",
                        {
                            "connection_id": str(connection_id),
                            "window_start": start.isoformat(),
                            "window_end": end.isoformat(),
                        },
                    )
                )
        background.add_task(dispatch, persist(jobs))
        return {"gmail_connection_id": str(ids[0]), "calendar_connection_id": str(ids[1])}

    @router.delete("/{connection_id}", status_code=204)
    def disconnect(connection_id: UUID) -> Response:
        row = find(connection_id)
        if row["provider"] not in ("gmail", "google_calendar"):
            raise ApiError(422, "unsupported_connection")
        with ctx.pool.connection() as conn:
            root = local_connections.slot_id(
                conn, workspace_id=workspace_id, connection_id=connection_id
            )
            if root is None:
                raise ApiError(404, "not_found")
            local_connections.disconnect(conn, workspace_id=workspace_id, slot_id=root)
        name = f"google_refresh_token:{root}"
        secrets.delete(name)
        send_secret_delete(name)
        return Response(status_code=204)

    @router.post("/{connection_id}/sync", status_code=202)
    def sync(connection_id: UUID, background: BackgroundTasks) -> dict[str, str]:
        row = find(connection_id)
        if row["status"] == "revoked":
            raise ApiError(409, "connection_unavailable")
        if row["provider"] not in ("gmail", "google_calendar"):
            raise ApiError(422, "unsupported_connection")
        key = f"incremental-now:{connection_id}:{uuid4()}"
        job = (connection_id, "sync.incremental", key, {"connection_id": str(connection_id)})
        background.add_task(dispatch, persist([job]))
        return {"status": "queued"}

    return router
