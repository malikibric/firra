"""POST /tasks/{task_type}: authenticated, idempotent, bounded-retry task execution.

Response codes drive Cloud Tasks: 2xx = done (no retry), 5xx = retry with queue backoff.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from firra.db.jobs import JobStore
from firra.logging import get_logger
from firra.tasks.auth import TaskAuthenticator, TaskAuthError
from firra.tasks.context import TaskContext
from firra.tasks.execute import continuation_key as continuation_key
from firra.tasks.execute import error_class_of as error_class_of
from firra.tasks.execute import execute_envelope
from firra.tasks.registry import REGISTRY
from firra_contracts import TaskEnvelope

log = get_logger(__name__)
router = APIRouter()


def get_authenticator(request: Request) -> TaskAuthenticator:
    authenticator: TaskAuthenticator = request.app.state.task_authenticator
    return authenticator


def get_job_store(request: Request) -> JobStore:
    store: JobStore = request.app.state.job_store
    return store


def get_task_context(request: Request) -> TaskContext:
    context: TaskContext = request.app.state.task_context
    return context


def authenticate(
    authenticator: Annotated[TaskAuthenticator, Depends(get_authenticator)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    try:
        authenticator.verify(authorization)
    except TaskAuthError as exc:
        log.warning("task.auth_rejected", error_class=error_class_of(exc))
        raise HTTPException(status_code=401, detail="unauthorized") from exc


@router.post("/tasks/{task_type}", dependencies=[Depends(authenticate)])
def run_task(
    task_type: str,
    envelope: TaskEnvelope,
    store: Annotated[JobStore, Depends(get_job_store)],
    context: Annotated[TaskContext, Depends(get_task_context)],
) -> JSONResponse:
    if envelope.type != task_type:
        raise HTTPException(status_code=400, detail="task type mismatch")
    spec = REGISTRY.get(task_type)
    if spec is None:
        raise HTTPException(status_code=400, detail="unknown task type")

    def requeue(continued: TaskEnvelope, delay: float) -> None:
        context.queue.enqueue(continued, delay_seconds=delay)

    result = execute_envelope(store, context, envelope, requeue=requeue)
    return JSONResponse(
        {"status": result.status}, status_code=503 if result.status == "retry" else 200
    )
