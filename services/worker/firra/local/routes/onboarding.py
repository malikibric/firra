"""Onboarding: client suggestions from mail headers, and re-reading mail when clients change.

Mail of clients that were not mapped yet was dropped at ingest (minimization). When the owner adds
a client, a domain or switches a client to Full, the mail connections read their window again
(once per hour at most, like the cloud flow), so the text of that client can be stored.
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from firra.db import local_connections, local_onboarding
from firra.db.types import WorkspaceId
from firra.local.readctx import ReadContext
from firra.local.server import ApiError
from firra.tasks.handlers.sync_common import month_windows
from firra_contracts import TaskEnvelope
from firra_contracts.generated.local.onboarding_suggestions import OnboardingSuggestions

Enqueue = Callable[[TaskEnvelope], None]


class SuggestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID


def _envelope(
    ctx: ReadContext, connection_id: UUID, job_type: str, key: str, payload: dict[str, Any]
) -> TaskEnvelope:
    workspace_id = WorkspaceId(ctx.identity.workspace_id)
    with ctx.pool.connection() as conn:
        job_id = local_connections.enqueue_job(
            conn,
            workspace_id=workspace_id,
            connection_id=connection_id,
            job_type=job_type,
            key=key,
            payload=payload,
        )
    return TaskEnvelope(
        workspace_id=workspace_id,
        job_id=job_id,
        idempotency_key=key,
        type=job_type,
        payload=payload,
    )


def read_mail_again(ctx: ReadContext, enqueue: Enqueue) -> None:
    """Re-run the backfill windows of every active mail connection once per client mapping.

    Mail of unmapped clients was discarded at ingest, so a new mapping must read history again.
    The key follows the mapping itself: saving several clients in a row reads once per distinct
    mapping, and adding another client later (even within the hour) reads again.
    """
    workspace_id = WorkspaceId(ctx.identity.workspace_id)
    now = ctx.clock()
    with ctx.pool.connection() as conn:
        connections = local_onboarding.gmail_connections(conn, workspace_id)
        nonce = local_onboarding.mapping_fingerprint(conn, workspace_id)
        local_onboarding.supersede_queued_rereads(conn, workspace_id)
    for connection in connections:
        for start, end in month_windows(now, connection["months"]):
            payload = {
                "connection_id": str(connection["id"]),
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
            }
            enqueue(
                _envelope(
                    ctx,
                    connection["id"],
                    "sync.backfill_chunk",
                    f"reread:{connection['id']}:{start:%Y-%m}:{nonce}",
                    payload,
                )
            )


def build_router(ctx: ReadContext, *, enqueue: Enqueue) -> APIRouter:
    router = APIRouter(prefix="/local/onboarding")
    workspace_id = WorkspaceId(ctx.identity.workspace_id)

    @router.post("/suggestions", status_code=202)
    def run(body: SuggestRequest) -> dict[str, str]:
        with ctx.pool.connection() as conn:
            rows = local_onboarding.gmail_connections(conn, workspace_id)
        if body.connection_id not in {r["id"] for r in rows}:
            raise ApiError(404, "not_found")
        with ctx.pool.connection() as conn:
            local_onboarding.supersede_queued_suggestions(conn, workspace_id)
        key = f"suggest:{body.connection_id}:{uuid4()}"
        enqueue(
            _envelope(
                ctx,
                body.connection_id,
                "onboarding.suggest",
                key,
                {"connection_id": str(body.connection_id)},
            )
        )
        return {"state": "running"}

    @router.get("/suggestions")
    def latest() -> Any:
        with ctx.pool.connection() as conn:
            job = local_onboarding.latest_suggestion_job(conn, workspace_id)
        if job is None:
            body = {"state": "none", "scanned": 0, "items": []}
        elif job["status"] == "succeeded":
            progress = job["progress"] or {}
            body = {
                "state": "ready",
                "scanned": progress.get("scanned", 0),
                "items": progress.get("suggestions", []),
            }
        elif job["status"] == "failed":
            body = {"state": "failed", "error_class": job["error_class"], "scanned": 0, "items": []}
        else:
            scanned = (job["progress"] or {}).get("scanned", 0)
            body = {"state": "running", "scanned": scanned, "items": []}
        return OnboardingSuggestions.model_validate(body).model_dump(mode="json")

    return router
