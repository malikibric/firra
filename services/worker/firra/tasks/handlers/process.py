"""process.records: drain a workspace's pending records in batches (P2 D7).

Off unless FIRRA_PROCESSING_ENABLED is true (every record is a paid model call); records stay
``pending`` meanwhile. One processing job per workspace at a time: ``request_processing`` enqueues
only when records are pending and no processing job is queued or running, so records are never
sent to the model twice in parallel. The job continues in follow-up tasks when its time budget
runs out; job.progress keeps the counters. Records it leaves pending (budget stop, lost task) are
picked up again by the next request (after ingest, or from the 15-minute sweep).
"""

import os
import time
from datetime import UTC, datetime
from typing import Any, Literal

from firra.ai.executor import ExecutorNotInstalled, executor_for, is_local
from firra.ai.local import runtime_for
from firra.ai.processor import PROMPT_VERSION as PACKET_PROMPT_VERSION
from firra.ai.processor import PacketProcessor
from firra.db.ai_processing import AiProcessingRepo
from firra.db.jobs import ClaimedJob
from firra.db.processing import PROCESS_JOB, ProcessingRepo
from firra.db.types import WorkspaceId
from firra.extract.process import PROMPT_VERSION, ProcessStats, RecordProcessor
from firra.llm.prompts import load_prompt
from firra.llm.service import BudgetExceededError
from firra.local.settings import load as load_owner_settings
from firra.tasks.context import TaskContext
from firra.tasks.errors import ContinueTask, PermanentTaskError
from firra_contracts import ProcessRecordsPayload, TaskEnvelope

BATCH_SIZE = 20


def request_processing(
    ctx: TaskContext, workspace_id: WorkspaceId, trigger: Literal["ingest", "sweep"]
) -> bool:
    """Enqueues process.records for the workspace when there is work and nobody is on it."""
    local = os.environ.get("FIRRA_PROFILE") == "local" or is_local(ctx.pool)
    if (ctx.llm is None and not local) or not ctx.processing_enabled:
        return False
    runtime = runtime_for(ctx.pool)
    if runtime is not None:
        return workspace_id == runtime.workspace_id and runtime.request(ctx, trigger)
    repo = ProcessingRepo(ctx.pool)
    if not repo.has_pending(workspace_id=workspace_id) or repo.has_active_job(
        workspace_id=workspace_id
    ):
        return False
    key = f"process:{datetime.now(UTC):%Y%m%dT%H%M}"
    payload = {"trigger": trigger}
    job_id = ctx.jobs.create(
        workspace_id=workspace_id, job_type=PROCESS_JOB, idempotency_key=key, payload=payload
    )
    if job_id is None:
        return False
    ctx.queue.enqueue(
        TaskEnvelope(
            workspace_id=workspace_id,
            job_id=job_id,
            idempotency_key=key,
            type=PROCESS_JOB,
            payload=payload,
        )
    )
    return True


def handle_process_records(
    ctx: TaskContext, job: ClaimedJob, payload: ProcessRecordsPayload
) -> dict[str, Any]:
    if os.environ.get("FIRRA_PROFILE") == "local" or is_local(ctx.pool):
        return _handle_local_process_records(ctx, job, payload)
    if ctx.llm is None:
        raise PermanentTaskError("llm_not_configured")
    if not ctx.processing_enabled:
        # A job queued before the switch was turned off; records stay pending.
        raise PermanentTaskError("processing_disabled")
    repo = ProcessingRepo(ctx.pool)
    processor = RecordProcessor(
        llm=ctx.llm,
        repo=repo,
        prompt=load_prompt(PROMPT_VERSION),
        workspace_id=job.workspace_id,
        job_id=job.id,
        stats=ProcessStats.from_progress(job.progress),
    )
    deadline = time.monotonic() + ctx.time_budget_seconds
    while True:
        batch = repo.pending(workspace_id=job.workspace_id, limit=BATCH_SIZE)
        if not batch:
            _request_memory(ctx, job)
            return {
                **processor.stats.as_progress(),
                "trigger": payload.trigger.value,
                "completed_at": datetime.now(UTC).isoformat(),
            }
        for record in batch:
            try:
                with repo.record_progress(job=job, progress=processor.stats.as_progress):
                    processor.process(record)
            except BudgetExceededError as exc:
                # Records stay pending; the next request after the budget allows it resumes.
                raise PermanentTaskError("budget_exceeded") from exc
            if time.monotonic() >= deadline:
                raise ContinueTask(processor.stats.as_progress())


def _handle_local_process_records(
    ctx: TaskContext, job: ClaimedJob, payload: ProcessRecordsPayload
) -> dict[str, Any]:
    if not ctx.processing_enabled:
        raise PermanentTaskError("processing_disabled")
    runtime = runtime_for(ctx.pool)
    revision = load_owner_settings(runtime.home).ai_revision if runtime else 0
    clock = runtime.clock if runtime else lambda: datetime.now(UTC)
    resume_at = job.progress.get("retry_at") or job.progress.get("resume_at")
    if resume_at and job.progress.get("ai_settings_revision", 0) == revision:
        delay = (datetime.fromisoformat(resume_at) - clock()).total_seconds()
        if delay > 0:
            raise ContinueTask(job.progress, delay)
    ctx.jobs.update_progress(
        workspace_id=job.workspace_id,
        job_id=job.id,
        progress={"ai_settings_revision": revision, "resume_at": None, "retry_at": None},
    )
    executor = executor_for(ctx.pool)
    if executor is None:
        raise PermanentTaskError(ExecutorNotInstalled().error_class)
    repo = AiProcessingRepo(ctx.pool)
    processor = PacketProcessor(
        executor=executor,
        repo=repo,
        prompt=load_prompt(PACKET_PROMPT_VERSION),
        workspace_id=job.workspace_id,
        stats=ProcessStats.from_progress(job.progress),
        input_tokens=int(job.progress.get("input_tokens", 0)),
        output_tokens=int(job.progress.get("output_tokens", 0)),
        timeout_seconds=min(120, ctx.time_budget_seconds),
        batch_records=(runtime.settings().batch_records or BATCH_SIZE) if runtime else BATCH_SIZE,
        clock=clock,
    )

    def progress() -> dict[str, Any]:
        return {**processor.progress(), "ai_settings_revision": revision}

    deadline = time.monotonic() + ctx.time_budget_seconds
    while True:
        batch = repo.pending(workspace_id=job.workspace_id, limit=processor.batch_records)
        if not batch:
            _request_memory(ctx, job)
            return {
                **progress(),
                "trigger": payload.trigger.value,
                "completed_at": datetime.now(UTC).isoformat(),
            }
        continuation: ContinueTask | None = None
        with repo.record_progress(job=job, progress=progress):
            try:
                processor.process(batch)
            except ContinueTask as exc:
                # Commit pre-check outcomes and counters before delaying the remaining work.
                continuation = exc
        if continuation is not None:
            raise ContinueTask(progress(), continuation.delay_seconds)
        if time.monotonic() >= deadline:
            raise ContinueTask(progress())


def _request_memory(ctx: TaskContext, job: ClaimedJob) -> None:
    key = f"memory:{job.id}"
    payload = {"trigger": "processing", "as_of": None}
    job_id = ctx.jobs.create(
        workspace_id=job.workspace_id,
        job_type="memory.rebuild",
        idempotency_key=key,
        payload=payload,
    )
    if job_id is not None:
        ctx.queue.enqueue(
            TaskEnvelope(
                workspace_id=job.workspace_id,
                job_id=job_id,
                idempotency_key=key,
                type="memory.rebuild",
                payload=payload,
            )
        )
