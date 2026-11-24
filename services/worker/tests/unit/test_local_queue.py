"""Local HTTP delivery: explicit retry, bounded budgets and uncertain outcomes."""

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest

from firra.tasks.queue import (
    LocalDeliveryUncertainError,
    LocalDispatchError,
    LocalQueue,
    LocalTaskFailedError,
)
from firra_contracts import TaskEnvelope
from tests.conftest import LOCAL_SECRET


@pytest.fixture
def envelope() -> TaskEnvelope:
    return TaskEnvelope(
        workspace_id=uuid4(),
        job_id=uuid4(),
        type="process.records",
        idempotency_key="local-retry",
        payload={"trigger": "sweep"},
    )


@pytest.fixture
def job(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], MagicMock]:
    row: dict[str, Any] = {"status": "queued", "attempts": 0, "max_attempts": 3}
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = row
    pool = MagicMock()
    pool.__enter__.return_value = pool
    pool.connection.return_value = conn
    monkeypatch.setattr("firra.tasks.queue.create_pool_for", lambda *a, **k: pool)
    monkeypatch.setenv("FIRRA_DATABASE_URL", "postgresql://localhost/test")
    return row, conn


def test_503_503_200_redelivers_with_backoff(
    envelope: TaskEnvelope,
    job: tuple[dict[str, Any], MagicMock],
) -> None:
    row, _ = job
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        row["attempts"] += 1
        return httpx.Response(503 if len(requests) < 3 else 200)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        LocalQueue("http://worker", LOCAL_SECRET, client, sleeps.append)._post(envelope)
    assert len(requests) == 3
    assert sleeps == [1, 2]
    assert all(r.content == envelope.model_dump_json().encode() for r in requests)
    assert all(r.headers["authorization"].startswith("Bearer ") for r in requests)


def test_503_exhaustion_commits_failed_state_before_raising(
    envelope: TaskEnvelope,
    job: tuple[dict[str, Any], MagicMock],
) -> None:
    row, conn = job
    deliveries = 0
    sleeps: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal deliveries
        deliveries += 1
        row["attempts"] += 1
        return httpx.Response(503)

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(LocalTaskFailedError, match="budget exhausted"),
    ):
        LocalQueue("http://worker", LOCAL_SECRET, client, sleeps.append)._post(envelope)
    assert deliveries == 3
    assert sleeps == [1, 2]
    sql, params = conn.execute.call_args.args
    assert "status = 'failed'" in sql and "finished_at = now()" in sql
    assert params == {"workspace_id": envelope.workspace_id, "job_id": envelope.job_id}
    assert conn.__exit__.call_args.args == (None, None, None)  # transaction commits


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 500])
def test_permanent_http_error_never_retries(envelope: TaskEnvelope, status: int) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, text="private response text")

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(LocalDispatchError, match=f"HTTP {status}") as error,
    ):
        LocalQueue("http://worker", LOCAL_SECRET, client)._post(envelope)
    assert len(requests) == 1
    assert "private" not in str(error.value)


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
def test_transport_failure_does_not_resend_or_change_job(
    envelope: TaskEnvelope,
    job: tuple[dict[str, Any], MagicMock],
    error_type: type[httpx.TransportError],
) -> None:
    _, conn = job
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise error_type("private transport detail", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(LocalDeliveryUncertainError) as error,
    ):
        LocalQueue("http://worker", LOCAL_SECRET, client)._post(envelope)
    assert len(requests) == 1
    conn.execute.assert_not_called()
    assert "private" not in str(error.value)


def test_preexisting_attempts_and_job_specific_limit(
    envelope: TaskEnvelope,
    job: tuple[dict[str, Any], MagicMock],
) -> None:
    row, _ = job
    row.update(attempts=3, max_attempts=4)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        row["attempts"] += 1
        return httpx.Response(503)

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(LocalTaskFailedError),
    ):
        LocalQueue("http://worker", LOCAL_SECRET, client)._post(envelope)
    assert len(requests) == 1


def test_503_without_claim_is_bounded_and_backoff_is_capped(
    envelope: TaskEnvelope,
    job: tuple[dict[str, Any], MagicMock],
) -> None:
    row, _ = job
    row["max_attempts"] = 8
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503)

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(LocalTaskFailedError),
    ):
        LocalQueue("http://worker", LOCAL_SECRET, client, sleeps.append)._post(envelope)
    assert len(requests) == 8
    assert sleeps == [1, 2, 4, 8, 16, 30, 30]


def test_503_does_not_replay_running_job(
    envelope: TaskEnvelope,
    job: tuple[dict[str, Any], MagicMock],
) -> None:
    row, conn = job
    row["status"] = "running"
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client,
        pytest.raises(LocalDispatchError, match="not queued"),
    ):
        LocalQueue("http://worker", LOCAL_SECRET, client)._post(envelope)
    assert conn.execute.call_count == 1


def test_terminal_worker_acknowledgement_surfaces_failure(envelope: TaskEnvelope) -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "failed"}))
        ) as client,
        pytest.raises(LocalTaskFailedError),
    ):
        LocalQueue("http://worker", LOCAL_SECRET, client)._post(envelope)


@pytest.mark.parametrize("status", [200, 201, 202, 204])
def test_success_acknowledges_without_job_lookup(
    envelope: TaskEnvelope, job: tuple[dict[str, Any], MagicMock], status: int
) -> None:
    _, conn = job
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as client:
        LocalQueue("http://worker", LOCAL_SECRET, client)._post(envelope)
    conn.execute.assert_not_called()


@pytest.mark.parametrize("state", ["succeeded", "failed", "cancelled"])
def test_terminal_job_is_never_replayed_or_overwritten(
    envelope: TaskEnvelope, job: tuple[dict[str, Any], MagicMock], state: str
) -> None:
    row, conn = job
    row["status"] = state
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        queue = LocalQueue("http://worker", LOCAL_SECRET, client)
        if state == "succeeded":
            queue._post(envelope)
        else:
            with pytest.raises(LocalDispatchError):
                queue._post(envelope)
    assert len(requests) == 1
    assert conn.execute.call_count == 1


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_retry_dispatch_refused_outside_local_test(
    envelope: TaskEnvelope,
    job: tuple[dict[str, Any], MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    env: str,
) -> None:
    _, conn = job
    monkeypatch.setenv("FIRRA_ENV", env)
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client,
        pytest.raises(LocalDispatchError, match="only allowed in local/test"),
    ):
        LocalQueue("http://worker", LOCAL_SECRET, client)._post(envelope)
    conn.execute.assert_not_called()


def test_retry_database_must_be_local(
    envelope: TaskEnvelope,
    job: tuple[dict[str, Any], MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, conn = job
    monkeypatch.setenv("FIRRA_DATABASE_URL", "postgresql://remote.invalid/test")
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client,
        pytest.raises(LocalDispatchError, match="local Postgres"),
    ):
        LocalQueue("http://worker", LOCAL_SECRET, client)._post(envelope)
    conn.execute.assert_not_called()
