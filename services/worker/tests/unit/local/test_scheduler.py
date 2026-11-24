"""Scheduler clock jumps, lifecycle and isolated sweep steps, without providers."""

import threading
import time
from datetime import UTC, date, datetime
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from tests.unit.local.test_executor import until

from firra.db.routines import DueRoutine
from firra.db.types import WorkspaceId
from firra.local.scheduler import LocalScheduler
from firra.maintenance.sweep import SweepDependencies, run_sweep
from firra.tasks.context import TaskContext


def test_startup_tick_sleep_and_prompt_stop() -> None:
    clocks = [0.0, datetime(2026, 10, 5, 9, tzinfo=UTC).timestamp()]
    calls: list[datetime] = []

    def sweep(ctx: TaskContext, now: datetime) -> Any:
        calls.append(now)
        return {}

    scheduler = LocalScheduler(
        cast(TaskContext, MagicMock()),
        interval=300,
        monotonic=lambda: clocks[0],
        wall=lambda: clocks[1],
        sweep=sweep,
    )
    scheduler.start()
    try:
        until(lambda: len(calls) == 1)
        with scheduler._condition:
            clocks[1] += 3 * 86400
            scheduler._condition.notify_all()
        until(lambda: len(calls) == 2)
        assert calls[-1].date() == date(2026, 10, 8)
        with scheduler._condition:
            clocks[0] += 300
            clocks[1] += 300
            scheduler._condition.notify_all()
        until(lambda: len(calls) == 3)
    finally:
        started = time.monotonic()
        scheduler.stop()
        assert time.monotonic() - started < 0.5


def test_pokes_during_sweep_coalesce_and_failures_retry() -> None:
    entered, release = threading.Event(), threading.Event()
    calls = []

    def sweep(ctx: TaskContext, now: datetime) -> Any:
        calls.append(now)
        if len(calls) == 1:
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test release timeout")
            raise ValueError("PRIVATE_MARKER")
        return {}

    scheduler = LocalScheduler(cast(TaskContext, MagicMock()), sweep=sweep)
    scheduler.start()
    try:
        assert entered.wait(3)
        for _ in range(20):
            scheduler.poke()
        release.set()
        until(lambda: len(calls) == 2)
        scheduler.stop()
        assert len(calls) == 2
    finally:
        release.set()
        scheduler.stop()


def dependencies(**changes: Any) -> SweepDependencies:
    repo = MagicMock()
    repo.due_routines.return_value = []
    values = dict(
        active_connections=lambda pool: [],
        RoutinesRepo=lambda pool: repo,
        claim_stuck_jobs=lambda pool: [],
        workspaces_with_pending=lambda pool: [],
        workspaces_with_meetings=lambda *args, **kw: [],
        load_meeting_config=lambda: None,
        request_processing=lambda *args: False,
    )
    return SweepDependencies(**{**values, **changes})


def test_failure_isolation_and_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = cast(TaskContext, MagicMock())
    called = MagicMock()
    monkeypatch.setattr("firra.maintenance.sweep.retention", called)

    def broken(pool: Any) -> Any:
        raise RuntimeError("PRIVATE_MARKER")

    counts = run_sweep(ctx, datetime.now(UTC), dependencies=dependencies(active_connections=broken))
    assert counts == dict(enqueued=0, routines=0, resumed=0, processing=0, briefs=0)
    called.assert_called_once()


def test_three_day_gap_creates_only_today_once() -> None:
    ws, rid = WorkspaceId(uuid4()), uuid4()
    repo = MagicMock()
    repo.due_routines.return_value = [DueRoutine(ws, rid, date(2026, 10, 8), False)]
    ctx = cast(TaskContext, MagicMock())
    seen = set()

    def create(**kw: Any) -> Any:
        key = kw["idempotency_key"]
        if key in seen:
            return None
        seen.add(key)
        return uuid4()

    ctx.jobs.create.side_effect = create  # type: ignore[attr-defined]
    steps = dependencies(RoutinesRepo=lambda pool: repo)
    now = datetime(2026, 10, 8, 9, tzinfo=UTC)
    assert run_sweep(ctx, now, dependencies=steps)["routines"] == 1
    assert run_sweep(ctx, now, dependencies=steps)["routines"] == 0
    assert seen == {f"routine:{rid}:2026-10-08"}
