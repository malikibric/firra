"""Delivery behavior with a fake repository, handlers and monotonic clock."""

import threading
import time
from dataclasses import replace
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from tests.unit.test_task_router import FakeJob, InMemoryJobStore

from firra.db.jobs import ClaimedJob, JobStore
from firra.local.executor import NON_RESUMABLE, LocalExecutor
from firra.tasks.context import TaskContext
from firra.tasks.errors import ContinueTask, PermanentTaskError
from firra.tasks.registry import REGISTRY, TaskSpec
from firra_contracts import DiagnosticsPingPayload, TaskEnvelope


class Store(InMemoryJobStore):
    def claim(self, **kw: Any) -> ClaimedJob | None:
        job = super().claim(**kw)
        return replace(job, progress=self.jobs[job.id].progress) if job else None


class Repository:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.envelopes: dict[UUID, TaskEnvelope] = {}
        self.keys: dict[tuple[UUID, str], UUID] = {}

    def ensure(self, envelope: TaskEnvelope) -> TaskEnvelope:
        key = (envelope.workspace_id, envelope.idempotency_key)
        job_id = self.keys.get(key, envelope.job_id)
        if job_id not in self.envelopes:
            self.keys[key] = job_id
            self.envelopes[job_id] = envelope
            self.store.jobs[job_id] = FakeJob(envelope.workspace_id, envelope.type)
        return self.envelopes[job_id]

    def recover(self, non_resumable: frozenset[str]) -> list[TaskEnvelope]:
        result = []
        for job_id, envelope in self.envelopes.items():
            job = self.store.jobs[job_id]
            if job.status not in ("queued", "running"):
                continue
            if envelope.type in non_resumable:
                self.store.jobs[job_id] = replace(
                    job, status="failed", error_class="abandoned_on_restart"
                )
            else:
                result.append(envelope)
        return result


def context(store: JobStore) -> TaskContext:
    fake = MagicMock()
    return TaskContext(
        pool=fake,
        jobs=store,
        connections=fake,
        records=fake,
        raw=fake,
        keyring=fake,
        queue=fake,
        http=fake,
        google=fake,
        microsoft=fake,
        time_budget_seconds=10,
    )


def envelope(**updates: Any) -> TaskEnvelope:
    return TaskEnvelope.model_validate(
        {
            "workspace_id": uuid4(),
            "job_id": uuid4(),
            "idempotency_key": str(uuid4()),
            "type": "diagnostics.ping",
            "payload": {"requested_at": "2026-10-05T00:00:00Z"},
            **updates,
        }
    )


def until(predicate: Any) -> None:
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("executor did not settle")
        time.sleep(0.001)


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> Any:
    store = Store()
    repo = Repository(store)
    monkeypatch.setattr("firra.local.executor.LocalJobs", lambda pool: repo)
    clock = [0.0]
    executor = LocalExecutor(context(cast(JobStore, store)), clock=lambda: clock[0])
    yield executor, store, repo, clock
    executor.stop(wait=3)


def tick(executor: LocalExecutor, clock: list[float], at: float) -> None:
    with executor._condition:
        clock[0] = at
        executor._condition.notify_all()


def test_success_noop_idempotent_enqueue(runtime: Any) -> None:
    executor, store, repo, _ = runtime
    env = envelope()
    executor.enqueue(env)
    executor.enqueue(env.model_copy(update={"job_id": uuid4()}))
    executor.start()
    until(lambda: store.jobs[env.job_id].status == "succeeded")
    executor.enqueue(env)
    until(lambda: not executor._pending and not executor._active)
    assert len(repo.envelopes) == 1
    assert store.jobs[env.job_id].attempts == 1


@pytest.mark.parametrize(
    "error,expected",
    [(PermanentTaskError("permanent"), "permanent"), (ValueError("fixture"), "value")],
)
def test_failure_and_retry_budget(
    runtime: Any, monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str
) -> None:
    executor, store, _, clock = runtime

    def handler(*args: Any) -> dict[str, Any]:
        raise error

    monkeypatch.setitem(REGISTRY, "diagnostics.ping", TaskSpec(DiagnosticsPingPayload, handler))
    env = envelope()
    executor.enqueue(env)
    executor.start()
    until(lambda: not executor._active and store.jobs[env.job_id].attempts == 1)
    if not isinstance(error, PermanentTaskError):
        assert executor._heap[0][0] == 5
        tick(executor, clock, 4)
        assert store.jobs[env.job_id].attempts == 1
        tick(executor, clock, 5)
        until(lambda: not executor._active and store.jobs[env.job_id].attempts == 2)
        assert executor._heap[0][0] == 15
        tick(executor, clock, 15)
    until(lambda: store.jobs[env.job_id].status == "failed")
    assert store.jobs[env.job_id].error_class == expected
    assert store.jobs[env.job_id].attempts == (1 if isinstance(error, PermanentTaskError) else 3)


def test_invalid_payload(runtime: Any) -> None:
    executor, store, _, _ = runtime
    env = envelope(payload={})
    executor.enqueue(env)
    executor.start()
    until(lambda: store.jobs[env.job_id].status == "failed")
    assert store.jobs[env.job_id].error_class == "invalid_payload"


def test_continuation_delay_and_no_worker_held(
    runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, store, _, clock = runtime

    def handler(
        ctx: TaskContext, job: ClaimedJob, payload: DiagnosticsPingPayload
    ) -> dict[str, Any]:
        if not job.progress:
            raise ContinueTask({"page": 1}, delay_seconds=20)
        return {"page": 2}

    monkeypatch.setitem(REGISTRY, "diagnostics.ping", TaskSpec(DiagnosticsPingPayload, handler))
    env = envelope()
    executor.enqueue(env)
    executor.start()
    until(lambda: store.jobs[env.job_id].progress == {"page": 1} and not executor._active)
    assert executor._heap[0][0] == 20
    assert store.jobs[env.job_id].attempts == 0
    tick(executor, clock, 20)
    until(lambda: store.jobs[env.job_id].status == "succeeded")
    assert store.jobs[env.job_id].progress == {"page": 2}


def test_per_job_exclusion_and_stop_leaves_queued(
    runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, store, _, _ = runtime
    entered, release = threading.Event(), threading.Event()
    calls = []

    def handler(*args: Any) -> dict[str, Any]:
        calls.append(1)
        entered.set()
        if not release.wait(3):
            raise RuntimeError("test release timeout")
        return {}

    monkeypatch.setitem(REGISTRY, "diagnostics.ping", TaskSpec(DiagnosticsPingPayload, handler))
    env, queued = envelope(), envelope()
    executor.enqueue(env)
    executor.start()
    assert entered.wait(3)
    try:
        executor.enqueue(env)
        executor.enqueue(queued, delay_seconds=60)
        started = time.monotonic()
        executor.stop(wait=0.01)
        assert time.monotonic() - started < 0.5
        assert store.jobs[queued.job_id].status == "queued"
        assert calls == [1]
    finally:
        release.set()
    until(lambda: store.jobs[env.job_id].status == "succeeded")


def test_start_recovers_all_types_except_probe(runtime: Any) -> None:
    executor, store, repo, _ = runtime
    env, probe = envelope(), envelope(type="diagnostics.llm_probe")
    repo.ensure(env)
    repo.ensure(probe)
    store.jobs[env.job_id] = replace(store.jobs[env.job_id], status="running")
    executor.start()
    until(lambda: store.jobs[env.job_id].status == "succeeded")
    assert {"diagnostics.llm_probe"} == NON_RESUMABLE
    assert store.jobs[probe.job_id].status == "failed"
    assert store.jobs[probe.job_id].error_class == "abandoned_on_restart"


def test_interactive_job_runs_while_background_workers_are_busy(
    runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, store, _, _ = runtime
    release = threading.Event()
    seen: list[str] = []

    def background(*args: Any) -> dict[str, Any]:
        seen.append("background")
        release.wait(3)
        return {}

    def interactive(*args: Any) -> dict[str, Any]:
        seen.append("interactive")
        return {}

    monkeypatch.setitem(REGISTRY, "diagnostics.ping", TaskSpec(DiagnosticsPingPayload, background))
    monkeypatch.setitem(
        REGISTRY, "onboarding.suggest", TaskSpec(DiagnosticsPingPayload, interactive)
    )
    slow = [envelope() for _ in range(executor.workers + 1)]
    for env in slow:
        executor.enqueue(env)
    owner = envelope(type="onboarding.suggest")
    executor.enqueue(owner)
    executor.start()
    # Every regular worker is busy with background work, yet the owner's job is not stuck behind it.
    until(lambda: store.jobs[owner.job_id].status == "succeeded")
    assert seen.count("background") == executor.workers
    release.set()
    until(lambda: all(store.jobs[e.job_id].status == "succeeded" for e in slow))
