from collections.abc import Sequence
from datetime import date
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from firra.db.jobs import ClaimedJob
from firra.db.routines import RoutinesRepo
from firra.db.types import JobId, WorkspaceId
from firra.routines import runner
from firra.routines.config import load_config
from firra.routines.model import Candidate, ClientContext, Prior
from firra.tasks.context import TaskContext
from firra.tasks.handlers import routines
from firra.tasks.registry import REGISTRY
from firra_contracts.generated.tasks.routines_run import RoutinesRunPayload

WS = WorkspaceId(UUID(int=1))
DAY = date(2026, 10, 6)
JOB = ClaimedJob(WS, JobId(UUID(int=2)), "routines.run", 1, 5)
PAYLOAD = RoutinesRunPayload(routine_id=UUID(int=3), scheduled_for=DAY)
RUN = UUID(int=4)


class FakeSource:
    def __init__(self, items: Sequence[Candidate]) -> None:
        self.items = items
        self.calls: list[tuple[RoutinesRepo, WorkspaceId, date]] = []

    def candidates(
        self, repo: RoutinesRepo, *, workspace_id: WorkspaceId, as_of: date
    ) -> Sequence[Candidate]:
        self.calls.append((repo, workspace_id, as_of))
        return self.items


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> tuple[TaskContext, MagicMock]:
    repo = MagicMock(spec=RoutinesRepo)
    repo.claim_run.return_value = {"id": RUN, "kind": "morning_scan", "enabled": True}
    monkeypatch.setattr(routines, "RoutinesRepo", lambda pool: repo)
    monkeypatch.setattr(runner, "SOURCES", {})
    return cast(TaskContext, MagicMock(spec=TaskContext, pool=MagicMock())), repo


@pytest.mark.parametrize("disabled", [False, True])
def test_no_source_and_disabled(setup: tuple[TaskContext, MagicMock], disabled: bool) -> None:
    ctx, repo = setup
    assert runner.SOURCES == {}
    repo.claim_run.return_value["enabled"] = not disabled
    source = FakeSource([])
    if disabled:
        runner.SOURCES["morning_scan"] = source
    expected = {"disabled": 1} if disabled else {"items": 0, "no_source": 1}
    assert routines.handle_routines_run(ctx, JOB, PAYLOAD) == expected
    repo.claim_run.assert_called_once_with(
        workspace_id=WS, routine_id=PAYLOAD.routine_id, scheduled_for=DAY
    )
    repo.finish_run.assert_called_once_with(
        workspace_id=WS, run_id=RUN, status="succeeded", counts=expected
    )
    repo.contexts.assert_not_called()
    repo.write_cards.assert_not_called()
    assert source.calls == []
    assert REGISTRY["routines.run"].handler is routines.handle_routines_run
    assert REGISTRY["routines.run"].payload_model is RoutinesRunPayload


def test_unclaimed_run_does_nothing(setup: tuple[TaskContext, MagicMock]) -> None:
    ctx, repo = setup
    repo.claim_run.return_value = None
    assert routines.handle_routines_run(ctx, JOB, PAYLOAD) == {"not_claimed": 1}
    repo.finish_run.assert_not_called()
    repo.contexts.assert_not_called()


def test_real_plan_suppresses_demotes_and_excludes(setup: tuple[TaskContext, MagicMock]) -> None:
    ctx, repo = setup
    clients = [UUID(int=n) for n in range(10, 14)]
    evidence = frozenset({UUID(int=20)})
    source = FakeSource(
        [
            Candidate(c, "state_change", "risk_up", DAY, risk_to="elevated", evidence_ids=evidence)
            for c in clients
        ]
    )
    runner.SOURCES["morning_scan"] = source
    repo.contexts.return_value = {
        c: ClientContext(c, "metadata_only" if c == clients[2] else "full", "elevated", None)
        for c in clients[:3]
    }
    repo.priors.return_value = {
        clients[0]: [Prior("state_change", "risk_rose_elevated", "wrong", DAY, evidence, 2)]
    }
    repo.important_used.return_value = load_config().budget_per_week - 1
    repo.write_cards.return_value = {"written": 2, "skipped_no_assessment": 0}
    counts = routines.handle_routines_run(ctx, JOB, PAYLOAD)
    assert counts == {
        "items": 2,
        "important": 1,
        "demoted": 1,
        "suppressed": 1,
        "skipped_no_assessment": 0,
    }
    assert source.calls == [(repo, WS, DAY)]
    repo.priors.assert_called_once_with(workspace_id=WS, client_ids=clients[:3])
    repo.important_used.assert_called_once_with(workspace_id=WS, week_start=date(2026, 10, 5))
    saved = repo.write_cards.call_args.kwargs
    assert saved["workspace_id"] == WS and saved["run_id"] == RUN and saved["as_of"] == DAY
    assert {d.client_id for d in saved["decisions"]} == set(clients[1:3])
    assert saved["decisions"][1].demoted_reason == "budget"
    assert saved["decisions"][1].evidence_ids == frozenset()
    repo.finish_run.assert_called_once_with(
        workspace_id=WS, run_id=RUN, status="succeeded", counts=counts
    )


@pytest.mark.parametrize("failure", ["source", "write"])
def test_failure_stores_class_only_and_rethrows(
    setup: tuple[TaskContext, MagicMock], failure: str
) -> None:
    ctx, repo = setup
    source = MagicMock()
    source.candidates.return_value = []
    runner.SOURCES["morning_scan"] = source
    repo.contexts.return_value = {}
    repo.priors.return_value = {}
    repo.important_used.return_value = 0
    failing = source.candidates if failure == "source" else repo.write_cards
    failing.side_effect = ValueError("private exception text")
    with pytest.raises(ValueError, match="private exception text"):
        routines.handle_routines_run(ctx, JOB, PAYLOAD)
    repo.finish_run.assert_called_once_with(
        workspace_id=WS, run_id=RUN, status="failed", counts={}, error_class="ValueError"
    )
