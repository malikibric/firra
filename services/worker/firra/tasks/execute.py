"""Transport-independent task execution; outcomes preserve the HTTP dispatcher semantics."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from firra.db.jobs import JobStore
from firra.db.types import JobId, WorkspaceId
from firra.http import ProviderError
from firra.logging import get_logger
from firra.tasks.context import TaskContext
from firra.tasks.errors import ContinueTask, PermanentTaskError
from firra.tasks.registry import REGISTRY
from firra_contracts import TaskEnvelope

log = get_logger("firra.tasks.router")
CONTINUATION = "#cont-"


@dataclass(frozen=True)
class TaskResult:
    status: Literal["noop", "continued", "failed", "retry", "succeeded"]
    attempt: int = 0
    delay_seconds: float = 0


def continuation_key(key: str) -> str:
    """A fresh key for the next run of a job: the job's own key plus one random suffix.

    The suffix replaces the previous one, so keys stay within the envelope's 200 characters however
    often a job continues or waits.
    """
    return f"{key.split(CONTINUATION, 1)[0]}{CONTINUATION}{uuid4().hex[:12]}"


def error_class_of(exc: BaseException) -> str:
    """Exception class -> snake_case error class (e.g. TokenRevokedError -> token_revoked)."""
    name = type(exc).__name__.removesuffix("Error") or type(exc).__name__
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def execute_envelope(
    store: JobStore,
    context: TaskContext,
    envelope: TaskEnvelope,
    *,
    requeue: Callable[[TaskEnvelope, float], None],
) -> TaskResult:
    task_type = envelope.type
    spec = REGISTRY.get(task_type)
    if spec is None:
        raise ValueError("unknown task type")

    workspace_id = WorkspaceId(envelope.workspace_id)
    job_id = JobId(envelope.job_id)
    fields: dict[str, Any] = {
        "task_type": task_type,
        "workspace_id": workspace_id,
        "job_id": job_id,
    }

    job = store.claim(workspace_id=workspace_id, job_id=job_id, job_type=task_type)
    if job is None:
        # Already finished, or unknown: acknowledge so Cloud Tasks stops redelivering.
        log.info("task.noop", **fields)
        return TaskResult("noop")
    fields["attempt"] = job.attempts

    try:
        payload = spec.payload_model.model_validate(envelope.payload)
        progress = spec.handler(context, job, payload)
    except ContinueTask as cont:
        # Resume point is in job.progress; a retry of this task would resume there too.
        store.requeue(workspace_id=workspace_id, job_id=job_id, progress=cont.progress)
        requeue(
            envelope.model_copy(
                update={"idempotency_key": continuation_key(envelope.idempotency_key)}
            ),
            cont.delay_seconds,
        )
        log.info("task.continued", delay_seconds=cont.delay_seconds, **fields)
        return TaskResult("continued", job.attempts, cont.delay_seconds)
    except (ValidationError, PermanentTaskError) as exc:
        error_class = exc.error_class if isinstance(exc, PermanentTaskError) else "invalid_payload"
        store.fail(workspace_id=workspace_id, job_id=job_id, error_class=error_class, final=True)
        log.error("task.failed", error_class=error_class, final=True, **fields)
        return TaskResult("failed", job.attempts)
    except Exception as exc:
        final = job.attempts >= job.max_attempts
        error_class = error_class_of(exc)
        if isinstance(exc, ProviderError):
            fields |= {"provider_status": exc.status, "provider_reason": exc.reason}
            forget = getattr(getattr(context, "google", None), "forget_access_tokens", None)
            if exc.status == 401 and callable(forget):
                forget()  # a rejected access token must not be served again on the retry
        store.fail(workspace_id=workspace_id, job_id=job_id, error_class=error_class, final=final)
        log.error("task.failed", exc=True, error_class=error_class, final=final, **fields)
        if final:
            return TaskResult("failed", job.attempts)
        return TaskResult("retry", job.attempts)

    store.succeed(workspace_id=workspace_id, job_id=job_id, progress=progress)
    log.info("task.succeeded", **fields)
    return TaskResult("succeeded", job.attempts)
