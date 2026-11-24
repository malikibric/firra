from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from firra.db.jobs import ClaimedJob
from firra.db.types import JobId, WorkspaceId
from firra.http import ProviderError
from firra.tasks import registry
from firra.tasks.auth import TaskAuthenticator
from firra.tasks.errors import ContinueTask, PermanentTaskError
from firra.tasks.registry import TaskSpec
from firra.tasks.router import continuation_key, error_class_of, router
from firra_contracts import DiagnosticsPingPayload
from tests.conftest import LOCAL_SECRET, local_settings
from tests.tokens import local_token


@dataclass
class FakeJob:
    workspace_id: UUID
    type: str
    status: str = "queued"
    attempts: int = 0
    max_attempts: int = 3
    error_class: str | None = None
    progress: dict[str, Any] = field(default_factory=dict)


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[UUID, FakeJob] = {}

    def claim(
        self, *, workspace_id: WorkspaceId, job_id: JobId, job_type: str
    ) -> ClaimedJob | None:
        job = self.jobs.get(job_id)
        if (
            job is None
            or job.workspace_id != workspace_id
            or job.type != job_type
            or job.status not in ("queued", "running")
        ):
            return None
        self.jobs[job_id] = replace(job, status="running", attempts=job.attempts + 1)
        claimed = self.jobs[job_id]
        return ClaimedJob(workspace_id, job_id, job_type, claimed.attempts, claimed.max_attempts)

    def succeed(
        self, *, workspace_id: WorkspaceId, job_id: JobId, progress: dict[str, Any]
    ) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = replace(job, status="succeeded", progress={**job.progress, **progress})

    def update_progress(
        self, *, workspace_id: WorkspaceId, job_id: JobId, progress: dict[str, Any]
    ) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = replace(job, progress={**job.progress, **progress})

    def requeue(
        self, *, workspace_id: WorkspaceId, job_id: JobId, progress: dict[str, Any]
    ) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = replace(
            job, status="queued", attempts=0, progress={**job.progress, **progress}
        )

    def fail(
        self, *, workspace_id: WorkspaceId, job_id: JobId, error_class: str, final: bool
    ) -> None:
        job = self.jobs[job_id]
        status = "failed" if final else "queued"
        self.jobs[job_id] = replace(job, status=status, error_class=error_class)


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def client(store: InMemoryJobStore) -> TestClient:
    app = FastAPI()
    app.state.task_authenticator = TaskAuthenticator(settings=local_settings(), jwks=None)  # type: ignore[arg-type]
    app.state.job_store = store
    app.state.task_context = None  # diagnostics.ping needs no dependencies
    app.include_router(router)
    return TestClient(app)


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"}


def envelope(
    workspace_id: UUID, job_id: UUID, task_type: str = "diagnostics.ping"
) -> dict[str, Any]:
    return {
        "workspace_id": str(workspace_id),
        "job_id": str(job_id),
        "idempotency_key": f"k-{job_id}",
        "type": task_type,
        "payload": {"requested_at": "2026-09-27T10:00:00Z"},
    }


def add_job(
    store: InMemoryJobStore, task_type: str = "diagnostics.ping", **kw: Any
) -> tuple[UUID, UUID]:
    workspace_id, job_id = uuid4(), uuid4()
    store.jobs[job_id] = FakeJob(workspace_id=workspace_id, type=task_type, **kw)
    return workspace_id, job_id


def test_requires_auth(client: TestClient, store: InMemoryJobStore) -> None:
    ws, job = add_job(store)
    assert client.post("/tasks/diagnostics.ping", json=envelope(ws, job)).status_code == 401
    assert store.jobs[job].status == "queued"


def test_ping_succeeds_and_records_progress(client: TestClient, store: InMemoryJobStore) -> None:
    ws, job = add_job(store)
    response = client.post("/tasks/diagnostics.ping", json=envelope(ws, job), headers=auth())
    assert response.status_code == 200
    assert response.json() == {"status": "succeeded"}
    assert store.jobs[job].status == "succeeded"
    assert "pong_at" in store.jobs[job].progress


def test_redelivery_is_a_noop(client: TestClient, store: InMemoryJobStore) -> None:
    ws, job = add_job(store)
    client.post("/tasks/diagnostics.ping", json=envelope(ws, job), headers=auth())
    again = client.post("/tasks/diagnostics.ping", json=envelope(ws, job), headers=auth())
    assert again.json() == {"status": "noop"}
    assert store.jobs[job].attempts == 1


def test_job_of_other_workspace_is_not_touched(client: TestClient, store: InMemoryJobStore) -> None:
    _, job = add_job(store)
    response = client.post("/tasks/diagnostics.ping", json=envelope(uuid4(), job), headers=auth())
    assert response.json() == {"status": "noop"}
    assert store.jobs[job].status == "queued"


def test_type_mismatch_and_unknown_type(client: TestClient, store: InMemoryJobStore) -> None:
    ws, job = add_job(store)
    mismatch = client.post("/tasks/other.task", json=envelope(ws, job), headers=auth())
    assert mismatch.status_code == 400
    unknown = client.post("/tasks/other.task", json=envelope(ws, job, "other.task"), headers=auth())
    assert unknown.status_code == 400


def test_invalid_payload_fails_permanently(client: TestClient, store: InMemoryJobStore) -> None:
    ws, job = add_job(store)
    body = envelope(ws, job) | {"payload": {"requested_at": "not a date"}}
    response = client.post("/tasks/diagnostics.ping", json=body, headers=auth())
    assert response.json() == {"status": "failed"}
    assert store.jobs[job].status == "failed"
    assert store.jobs[job].error_class == "invalid_payload"


class TokenRevokedError(Exception):
    pass


def _raise(exc: Exception) -> Any:
    def handler(*_args: Any) -> Any:
        raise exc

    return handler


def test_transient_errors_retry_until_max_attempts(
    client: TestClient, store: InMemoryJobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = TaskSpec(DiagnosticsPingPayload, _raise(TokenRevokedError()))
    monkeypatch.setitem(registry.REGISTRY, "diagnostics.ping", spec)
    ws, job = add_job(store, max_attempts=2)

    first = client.post("/tasks/diagnostics.ping", json=envelope(ws, job), headers=auth())
    assert first.status_code == 503
    assert store.jobs[job].status == "queued"

    second = client.post("/tasks/diagnostics.ping", json=envelope(ws, job), headers=auth())
    assert second.status_code == 200
    assert second.json() == {"status": "failed"}
    assert store.jobs[job].status == "failed"
    assert store.jobs[job].error_class == "token_revoked"


def test_permanent_error_fails_immediately(
    client: TestClient, store: InMemoryJobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = TaskSpec(DiagnosticsPingPayload, _raise(PermanentTaskError("scope_missing")))
    monkeypatch.setitem(registry.REGISTRY, "diagnostics.ping", spec)
    ws, job = add_job(store)
    response = client.post("/tasks/diagnostics.ping", json=envelope(ws, job), headers=auth())
    assert response.json() == {"status": "failed"}
    assert store.jobs[job].error_class == "scope_missing"


def test_error_class_of() -> None:
    assert error_class_of(TokenRevokedError()) == "token_revoked"
    assert error_class_of(TimeoutError()) == "timeout"


def test_auth_is_checked_before_body_validation(client: TestClient) -> None:
    # Unauthenticated callers must not learn the payload schema from validation errors.
    response = client.post("/tasks/diagnostics.ping", json={"nonsense": True})
    assert response.status_code == 401
    assert "detail" in response.json() and response.json()["detail"] == "unauthorized"


class RecordingQueue:
    def __init__(self) -> None:
        self.envelopes: list[Any] = []
        self.delays: list[float] = []

    def enqueue(self, envelope: Any, *, delay_seconds: float = 0) -> None:
        self.envelopes.append(envelope)
        self.delays.append(delay_seconds)


def test_continuation_requeues_the_job_and_enqueues_the_same_task(
    client: TestClient, store: InMemoryJobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = RecordingQueue()
    client.app.state.task_context = SimpleNamespace(queue=queue)  # type: ignore[attr-defined]
    spec = TaskSpec(DiagnosticsPingPayload, _raise(ContinueTask({"cursor": "page-2", "seen": 50})))
    monkeypatch.setitem(registry.REGISTRY, "diagnostics.ping", spec)
    ws, job = add_job(store, max_attempts=1)

    response = client.post("/tasks/diagnostics.ping", json=envelope(ws, job), headers=auth())
    assert response.json() == {"status": "continued"}
    saved = store.jobs[job]
    assert saved.status == "queued" and saved.attempts == 0
    assert saved.progress == {"cursor": "page-2", "seen": 50}
    (follow_up,) = queue.envelopes
    assert follow_up.job_id == job and follow_up.type == "diagnostics.ping"
    assert follow_up.idempotency_key.startswith(f"k-{job}#cont-")


def test_continuation_can_wait_before_the_next_run(
    client: TestClient, store: InMemoryJobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = RecordingQueue()
    client.app.state.task_context = SimpleNamespace(queue=queue)  # type: ignore[attr-defined]
    spec = TaskSpec(DiagnosticsPingPayload, _raise(ContinueTask({}, delay_seconds=60)))
    monkeypatch.setitem(registry.REGISTRY, "diagnostics.ping", spec)
    ws, job = add_job(store)

    response = client.post("/tasks/diagnostics.ping", json=envelope(ws, job), headers=auth())
    assert response.json() == {"status": "continued"}
    assert store.jobs[job].status == "queued" and store.jobs[job].attempts == 0
    assert queue.delays == [60]


def test_provider_errors_log_status_and_error_code(
    client: TestClient,
    store: InMemoryJobStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spec = TaskSpec(DiagnosticsPingPayload, _raise(ProviderError(403, "rateLimitExceeded")))
    monkeypatch.setitem(registry.REGISTRY, "diagnostics.ping", spec)
    ws, job = add_job(store)

    response = client.post("/tasks/diagnostics.ping", json=envelope(ws, job), headers=auth())
    assert response.status_code == 503
    (record,) = [r for r in caplog.records if r.getMessage() == "task.failed"]
    fields = record.fields  # type: ignore[attr-defined]
    assert fields["provider_status"] == 403
    assert fields["provider_reason"] == "rateLimitExceeded"


def test_continuation_keys_stay_short_however_often_a_job_continues() -> None:
    key = "backfill:0f8fad5b-d9cb-469f-a165-70867728950e:2026-09"
    for _ in range(100):
        key = continuation_key(key)
    assert key.startswith("backfill:0f8fad5b-d9cb-469f-a165-70867728950e:2026-09#cont-")
    assert len(key) < 100
