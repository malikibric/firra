"""Transport-independent maintenance steps; one failed step cannot prevent the next."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from firra.brief.meeting import load_config as load_meeting_config
from firra.connectors.google.local_tokens import LocalGoogleTokens
from firra.db.local_connections import active as local_active_connections
from firra.db.local_retention import purge_text_before
from firra.db.local_slack import active as local_slack_connections
from firra.db.meetings import workspaces_with_meetings
from firra.db.processing import workspaces_with_pending
from firra.db.routines import RoutinesRepo
from firra.db.sweeps import active_connections, claim_stuck_jobs
from firra.local.settings import load
from firra.logging import get_logger
from firra.tasks.context import TaskContext
from firra.tasks.execute import continuation_key, error_class_of
from firra.tasks.handlers.process import request_processing
from firra_contracts import TaskEnvelope

log = get_logger("firra.maintenance.router")


SweepCounts = dict[str, int]


@dataclass(frozen=True)
class SweepDependencies:
    active_connections: Callable[..., Any] = active_connections
    RoutinesRepo: Callable[..., Any] = RoutinesRepo
    claim_stuck_jobs: Callable[..., Any] = claim_stuck_jobs
    workspaces_with_pending: Callable[..., Any] = workspaces_with_pending
    workspaces_with_meetings: Callable[..., Any] = workspaces_with_meetings
    load_meeting_config: Callable[..., Any] = load_meeting_config
    request_processing: Callable[..., Any] = request_processing


def retention(context: TaskContext, now: datetime) -> None:
    """Local retention: text older than the owner's period is deleted (never the cloud purge)."""
    if context.home is None:
        return
    months = load(context.home).body_retention_months
    if months is None:
        return
    purged = purge_text_before(context.pool, now - timedelta(days=round(months * 30.44)))
    if purged:
        log.info("maintenance.retention", records=purged, months=months)


def run_sweep(
    context: TaskContext, now: datetime, *, dependencies: SweepDependencies | None = None
) -> SweepCounts:
    dependencies = dependencies or SweepDependencies()
    slot = f"{now:%Y%m%dT%H}q{now.minute // 15}"
    enqueued = routines = resumed = processing = briefs = 0
    workspaces = set()
    try:
        sync_slot = slot
        if isinstance(context.google, LocalGoogleTokens):
            sync_slot = (
                f"local:{int(now.timestamp()) // (context.google.sync_interval_minutes * 60)}"
            )
            if context.google.workspace_id is None:
                targets = []
            else:
                with context.pool.connection() as conn:
                    targets = local_active_connections(
                        conn, workspace_id=context.google.workspace_id
                    )
        else:
            targets = dependencies.active_connections(context.pool)
        for target in targets:
            workspaces.add(target.workspace_id)
            key = f"sweep:{target.connection_id}:{sync_slot}"
            payload = {"connection_id": str(target.connection_id)}
            job_id = context.jobs.create(
                workspace_id=target.workspace_id,
                job_type="sync.incremental",
                idempotency_key=key,
                payload=payload,
                connection_id=target.connection_id,
            )
            if job_id is None:
                continue
            context.queue.enqueue(
                TaskEnvelope(
                    workspace_id=target.workspace_id,
                    job_id=job_id,
                    idempotency_key=key,
                    type="sync.incremental",
                    payload=payload,
                )
            )
            enqueued += 1
    except Exception as exc:
        log.error("maintenance.step_failed", step="sync", error_class=error_class_of(exc))

    if (
        getattr(context, "slack", None) is not None
        and isinstance(context.google, LocalGoogleTokens)
        and context.google.workspace_id is not None
    ):
        workspace_id = context.google.workspace_id
        try:
            with context.pool.connection() as conn:
                ids = local_slack_connections(conn, workspace_id=workspace_id)
            for connection_id in ids:
                workspaces.add(workspace_id)
                key = f"slack-sweep:{connection_id}:{int(now.timestamp()) // 300}"
                payload = {"connection_id": str(connection_id)}
                job_id = context.jobs.create(
                    workspace_id=workspace_id,
                    job_type="sync.slack",
                    idempotency_key=key,
                    payload=payload,
                    connection_id=connection_id,
                )
                if job_id is not None:
                    context.queue.enqueue(
                        TaskEnvelope(
                            workspace_id=workspace_id,
                            job_id=job_id,
                            idempotency_key=key,
                            type="sync.slack",
                            payload=payload,
                        )
                    )
                    enqueued += 1
        except Exception as exc:
            log.error("maintenance.step_failed", step="slack_sync", error_class=error_class_of(exc))

    try:
        routine_repo = dependencies.RoutinesRepo(context.pool)
        for workspace_id in sorted(workspaces):
            routine_repo.ensure_routines(workspace_id=workspace_id)
        routines = 0
        due = routine_repo.due_routines(now=now)
        for routine in due:
            key = f"routine:{routine.routine_id}:{routine.local_date}"
            routine_payload = {
                "routine_id": str(routine.routine_id),
                "scheduled_for": routine.local_date.isoformat(),
            }
            job_id = context.jobs.create(
                workspace_id=routine.workspace_id,
                job_type="routines.run",
                idempotency_key=key,
                payload=routine_payload,
            )
            if job_id is not None:
                context.queue.enqueue(
                    TaskEnvelope(
                        workspace_id=routine.workspace_id,
                        job_id=job_id,
                        idempotency_key=key,
                        type="routines.run",
                        payload=routine_payload,
                    )
                )
                routines += 1
        log.info(
            "maintenance.routines",
            count=routines,
            unknown_timezone=sum(r.unknown_timezone for r in due),
        )
    except Exception as exc:
        log.error("maintenance.step_failed", step="routines", error_class=error_class_of(exc))

    try:
        resumed = 0
        for job in dependencies.claim_stuck_jobs(context.pool):
            context.queue.enqueue(
                TaskEnvelope(
                    workspace_id=job.workspace_id,
                    job_id=job.id,
                    idempotency_key=continuation_key(job.idempotency_key),
                    type=job.type,
                    payload=job.payload,
                )
            )
            resumed += 1
    except Exception as exc:
        log.error("maintenance.step_failed", step="resume", error_class=error_class_of(exc))

    try:
        processing = sum(
            dependencies.request_processing(context, workspace_id, "sweep")
            for workspace_id in dependencies.workspaces_with_pending(context.pool)
        )
    except Exception as exc:
        log.error("maintenance.step_failed", step="processing", error_class=error_class_of(exc))

    try:
        briefs = 0
        for workspace_id in dependencies.workspaces_with_meetings(
            context.pool, now=now, cfg=dependencies.load_meeting_config()
        ):
            key = f"brief:{workspace_id}:{slot}"
            payload = {"trigger": "sweep"}
            job_id = context.jobs.create(
                workspace_id=workspace_id,
                job_type="brief.meetings",
                idempotency_key=key,
                payload=payload,
            )
            if job_id is not None:
                context.queue.enqueue(
                    TaskEnvelope(
                        workspace_id=workspace_id,
                        job_id=job_id,
                        idempotency_key=key,
                        type="brief.meetings",
                        payload=payload,
                    )
                )
                briefs += 1
        log.info("maintenance.meeting_briefs", count=briefs)
    except Exception as exc:
        log.error("maintenance.step_failed", step="briefs", error_class=error_class_of(exc))
    try:
        retention(context, now)
    except Exception as exc:
        log.error("maintenance.step_failed", step="retention", error_class=error_class_of(exc))
    log.info("maintenance.sweep", count=enqueued, resumed=resumed, processing=processing)
    return {
        "enqueued": enqueued,
        "resumed": resumed,
        "processing": processing,
        "briefs": briefs,
        "routines": routines,
    }
