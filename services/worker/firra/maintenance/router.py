"""POST /maintenance/*: called by Cloud Scheduler with the same OIDC identity as Cloud Tasks."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from firra.brief.meeting import load_config as load_meeting_config
from firra.db.meetings import workspaces_with_meetings
from firra.db.processing import workspaces_with_pending
from firra.db.retention import purge_expired_bodies
from firra.db.routines import RoutinesRepo
from firra.db.sweeps import active_connections, claim_stuck_jobs
from firra.logging import get_logger
from firra.maintenance.sweep import SweepDependencies, run_sweep
from firra.tasks.context import TaskContext
from firra.tasks.handlers.process import request_processing
from firra.tasks.router import authenticate, get_task_context

log = get_logger(__name__)
router = APIRouter(prefix="/maintenance", dependencies=[Depends(authenticate)])


@router.post("/purge-bodies")
def purge_bodies(
    _request: Request, context: Annotated[TaskContext, Depends(get_task_context)]
) -> dict[str, int]:
    """Hourly event-driven purge, 72-hour fallback and 7-day cap with pending grace."""
    result = purge_expired_bodies(context.pool, context.raw)
    log.info(
        "maintenance.purge_bodies",
        count=result.purged,
        unprocessed=result.unprocessed,
        undelivered=result.undelivered,
    )
    return {
        "purged": result.purged,
        "purged_unprocessed": result.unprocessed,
        "purged_undelivered": result.undelivered,
    }


@router.post("/sweep")
def sweep(
    _request: Request, context: Annotated[TaskContext, Depends(get_task_context)]
) -> dict[str, int]:
    return run_sweep(
        context,
        datetime.now(UTC),
        dependencies=SweepDependencies(
            active_connections=active_connections,
            RoutinesRepo=RoutinesRepo,
            claim_stuck_jobs=claim_stuck_jobs,
            workspaces_with_pending=workspaces_with_pending,
            workspaces_with_meetings=workspaces_with_meetings,
            load_meeting_config=load_meeting_config,
            request_processing=request_processing,
        ),
    )
