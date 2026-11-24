"""memory.rebuild task: deterministic workspace account memory, no model calls."""

from datetime import UTC, datetime
from typing import Any

from firra.db.jobs import ClaimedJob
from firra.db.memory import MemoryRepo
from firra.db.needs import NeedsRepo
from firra.memory.rules import load_config
from firra.tasks.context import TaskContext
from firra_contracts import MemoryRebuildPayload, TaskEnvelope


def handle_memory_rebuild(
    ctx: TaskContext,
    job: ClaimedJob,
    payload: MemoryRebuildPayload,
) -> dict[str, Any]:
    as_of = payload.as_of or datetime.now(UTC).date()
    repo = MemoryRepo(ctx.pool)
    cfg = load_config()
    changed = 0
    clients = repo.clients(workspace_id=job.workspace_id)
    for client in clients:
        changed += repo.rebuild_client(
            workspace_id=job.workspace_id,
            client_id=client["id"],
            as_of=as_of,
            cfg=cfg,
        )
    key = f"signals:{job.id}"
    signals_payload = {
        "trigger": "memory",
        "as_of": payload.as_of.isoformat() if payload.as_of else None,
    }
    signals_job_id = ctx.jobs.create(
        workspace_id=job.workspace_id,
        job_type="signals.evaluate",
        idempotency_key=key,
        payload=signals_payload,
    )
    if signals_job_id is not None:
        ctx.queue.enqueue(
            TaskEnvelope(
                workspace_id=job.workspace_id,
                job_id=signals_job_id,
                idempotency_key=key,
                type="signals.evaluate",
                payload=signals_payload,
            )
        )
    needs_key = f"needs:{job.id}"
    needs_payload = {
        "trigger": "memory",
        "as_of": payload.as_of.isoformat() if payload.as_of else None,
    }
    needs_job_id = NeedsRepo(ctx.pool).request_job(
        workspace_id=job.workspace_id, key=needs_key, payload=needs_payload
    )
    if needs_job_id is not None:
        ctx.queue.enqueue(
            TaskEnvelope(
                workspace_id=job.workspace_id,
                job_id=needs_job_id,
                idempotency_key=needs_key,
                type="needs.update",
                payload=needs_payload,
            )
        )
    return {
        "clients": len(clients),
        "cards_changed": changed,
        "as_of": as_of.isoformat(),
        "trigger": payload.trigger.value,
    }
