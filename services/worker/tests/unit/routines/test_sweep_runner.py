from datetime import UTC, date, datetime
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi import Request

from firra.db.routines import DueRoutine
from firra.db.sweeps import SweepTarget, claim_stuck_jobs
from firra.db.types import JobId, WorkspaceId
from firra.maintenance import router
from firra.tasks.context import TaskContext


def test_sweep_initializes_once_and_dedupes_by_local_date(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = WorkspaceId(UUID(int=1))
    rid = UUID(int=2)
    seen: set[tuple[WorkspaceId, str]] = set()
    jobs = MagicMock()
    queue = MagicMock()

    def create(
        *, workspace_id: WorkspaceId, idempotency_key: str, **kwargs: object
    ) -> JobId | None:
        key = (workspace_id, idempotency_key)
        if key in seen:
            return None
        seen.add(key)
        return JobId(UUID(int=len(seen) + 10))

    jobs.create.side_effect = create
    ctx = cast(TaskContext, MagicMock(pool=MagicMock(), jobs=jobs, queue=queue))
    repo = MagicMock()
    repo.due_routines.return_value = [DueRoutine(ws, rid, date(2026, 10, 5), True)]
    monkeypatch.setattr(router, "RoutinesRepo", lambda pool: repo)
    monkeypatch.setattr(
        router,
        "active_connections",
        lambda pool: [SweepTarget(ws, UUID(int=3)), SweepTarget(ws, UUID(int=4))],
    )
    for name in ("claim_stuck_jobs", "workspaces_with_pending"):
        monkeypatch.setattr(router, name, lambda pool: [])
    monkeypatch.setattr(router, "workspaces_with_meetings", lambda *args, **kwargs: [])
    clock = MagicMock()
    clock.now.return_value = datetime(2026, 10, 5, 23, tzinfo=UTC)
    monkeypatch.setattr(router, "datetime", clock)
    request = cast(Request, MagicMock())
    assert router.sweep(request, ctx)["routines"] == 1
    assert router.sweep(request, ctx)["routines"] == 0
    assert repo.ensure_routines.call_count == 2  # Once per workspace, even when sync was deduped.
    repo.ensure_routines.assert_called_with(workspace_id=ws)
    routine_tasks = [
        call.args[0] for call in queue.enqueue.call_args_list if call.args[0].type == "routines.run"
    ]
    assert len(routine_tasks) == 1
    task = routine_tasks[0]
    assert task.workspace_id == ws and task.idempotency_key == f"routine:{rid}:2026-10-05"
    assert task.payload == {"routine_id": str(rid), "scheduled_for": "2026-10-05"}
    repo.due_routines.return_value = [DueRoutine(ws, rid, date(2026, 10, 6), False)]
    clock.now.return_value = datetime(2026, 10, 6, 23, tzinfo=UTC)
    assert router.sweep(request, ctx)["routines"] == 1


def test_stuck_routine_jobs_are_resumable() -> None:
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    conn.execute.return_value.fetchall.return_value = []
    assert claim_stuck_jobs(pool) == []
    params = conn.execute.call_args.args[1]
    assert "routines.run" in params["types"]
