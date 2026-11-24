"""Task adapter for deterministic first-party routines."""

from typing import Any

from firra.db.jobs import ClaimedJob
from firra.db.routines import RoutinesRepo
from firra.logging import get_logger
from firra.routines.runner import run
from firra.tasks.context import TaskContext
from firra_contracts.generated.tasks.routines_run import RoutinesRunPayload

log = get_logger(__name__)


def handle_routines_run(
    ctx: TaskContext, job: ClaimedJob, payload: RoutinesRunPayload
) -> dict[str, Any]:
    counts = run(
        RoutinesRepo(ctx.pool),
        workspace_id=job.workspace_id,
        routine_id=payload.routine_id,
        scheduled_for=payload.scheduled_for,
    )
    log.info(
        "routines.run",
        workspace_id=str(job.workspace_id),
        routine_id=str(payload.routine_id),
        counts=counts,
    )
    return counts
