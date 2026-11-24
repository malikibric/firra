from datetime import date, timedelta
from typing import Literal, cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from firra.db.routine_commitments import RoutineCommitmentsRepo
from firra.db.routines import RoutinesRepo
from firra.db.types import WorkspaceId
from firra.routines.model import Candidate
from firra.routines.runner import SOURCES
from firra.routines.sources import commitment_watch

DAY = date(2026, 10, 5)
WS = WorkspaceId(UUID(int=1))
CLIENT = UUID(int=2)
EVIDENCE = UUID(int=3)


@pytest.mark.parametrize(
    ("status", "due_back", "expected"),
    [
        ("open", 1, True),
        ("late", 1, True),
        ("open", 2, False),
        ("open", 0, False),
        ("open", -1, False),
        ("open", None, False),
        ("late", None, False),
        ("done", 1, False),
        ("cancelled", 1, False),
    ],
)
@pytest.mark.parametrize("owner", ["firm", "client"])
def test_late_transition(
    monkeypatch: pytest.MonkeyPatch, status: str, due_back: int | None, expected: bool, owner: str
) -> None:
    stored = MagicMock(spec=RoutineCommitmentsRepo)
    stored.since.return_value = DAY - timedelta(days=1)
    stored.commitments.return_value = [
        {
            "client_id": CLIENT,
            "status": status,
            "due_date": DAY - timedelta(days=due_back) if due_back is not None else None,
            "owner_side": owner,
            "evidence_ids": [EVIDENCE, EVIDENCE],
        }
    ]
    monkeypatch.setattr(commitment_watch, "RoutineCommitmentsRepo", lambda pool: stored)
    result = commitment_watch.CommitmentWatch().candidates(
        cast(RoutinesRepo, MagicMock()), workspace_id=WS, as_of=DAY
    )
    assert len(result) == int(expected)
    if result:
        assert result[0] == Candidate(
            CLIENT,
            "commitment_watch",
            "commitment_late",
            DAY,
            owner_side=cast(Literal["firm", "client"], owner),
            evidence_ids=frozenset({EVIDENCE}),
        )
    stored.since.assert_called_once_with(workspace_id=WS, as_of=DAY)
    stored.commitments.assert_called_once_with(workspace_id=WS, as_of=DAY)


@pytest.mark.parametrize(("previous", "days"), [(None, 1), (1, 1), (4, 4), (7, 7), (20, 7)])
def test_since_default_and_cap(previous: int | None, days: int) -> None:
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = {
        "day": DAY - timedelta(days=previous) if previous else None
    }
    assert RoutineCommitmentsRepo(pool).since(workspace_id=WS, as_of=DAY) == (
        DAY - timedelta(days=days)
    )
    params = conn.execute.call_args.args[1]
    assert params == {"workspace_id": WS, "as_of": DAY}


def test_one_candidate_per_commitment(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = MagicMock(spec=RoutineCommitmentsRepo)
    stored.since.return_value = DAY - timedelta(days=4)
    stored.commitments.return_value = [
        {
            "client_id": CLIENT,
            "status": "open",
            "due_date": DAY - timedelta(days=back),
            "owner_side": "firm",
            "evidence_ids": [UUID(int=back)],
        }
        for back in (1, 3, 4, 5)
    ]
    monkeypatch.setattr(commitment_watch, "RoutineCommitmentsRepo", lambda pool: stored)
    result = commitment_watch.CommitmentWatch().candidates(
        cast(RoutinesRepo, MagicMock()), workspace_id=WS, as_of=DAY
    )
    assert [c.evidence_ids for c in result] == [
        frozenset({UUID(int=1)}),
        frozenset({UUID(int=3)}),
        frozenset({UUID(int=4)}),
    ]
    assert all(c.client_id == CLIENT for c in result)


def test_registered_next_to_morning_scan() -> None:
    assert isinstance(SOURCES["commitment_watch"], commitment_watch.CommitmentWatch)
    assert "morning_scan" in SOURCES
