from datetime import date, timedelta
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from firra.db.routine_changes import RoutineChangesRepo
from firra.db.routines import RoutinesRepo
from firra.db.types import WorkspaceId
from firra.routines.sources import morning_scan

DAY = date(2026, 10, 5)
WS = WorkspaceId(UUID(int=1))
CLIENT = UUID(int=2)
EVIDENCE = UUID(int=3)


@pytest.fixture
def changes(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    repo = MagicMock(spec=RoutineChangesRepo)
    repo.since.return_value = DAY - timedelta(days=1)
    repo.assessments.return_value = []
    repo.signals.return_value = []
    repo.deadlines.return_value = []
    monkeypatch.setattr(morning_scan, "RoutineChangesRepo", lambda pool: repo)
    return repo


@pytest.mark.parametrize(
    ("old", "new", "trigger"),
    [
        ("stable", "watch", "risk_up"),
        ("watch", "elevated", "risk_up"),
        ("elevated", "high", "risk_up"),
        ("high", "watch", "risk_down"),
        ("watch", "watch", None),
        (None, "elevated", None),
    ],
)
def test_risk_mapping(changes: MagicMock, old: str | None, new: str, trigger: str | None) -> None:
    changes.assessments.return_value = [
        {
            "client_id": CLIENT,
            "risk_state": new,
            "previous_risk_state": old,
            "previous_expansion": "strong",
            "expansion_state": "none",
            "evidence_ids": [EVIDENCE],
        }
    ]
    result = morning_scan.MorningScan().candidates(
        cast(RoutinesRepo, MagicMock()), workspace_id=WS, as_of=DAY
    )
    assert len(result) == (0 if trigger is None else 1)
    if result:
        assert (result[0].trigger, result[0].kind, result[0].risk_to) == (
            trigger,
            "state_change",
            new,
        )
        assert result[0].evidence_ids == frozenset({EVIDENCE})
        assert result[0].as_of == DAY
    changes.assessments.assert_called_once_with(
        workspace_id=WS, since=DAY - timedelta(days=1), as_of=DAY
    )


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("none", "hypothesis", True),
        ("hypothesis", "strong", True),
        ("strong", "none", False),
        (None, "strong", False),
    ],
)
def test_expansion_mapping(changes: MagicMock, old: str | None, new: str, expected: bool) -> None:
    changes.assessments.return_value = [
        {
            "client_id": CLIENT,
            "risk_state": "stable",
            "previous_risk_state": None,
            "previous_expansion": old,
            "expansion_state": new,
            "evidence_ids": [],
        }
    ]
    result = morning_scan.MorningScan().candidates(
        cast(RoutinesRepo, MagicMock()), workspace_id=WS, as_of=DAY
    )
    assert len(result) == int(expected)
    if result:
        assert (result[0].trigger, result[0].kind, result[0].expansion_to) == (
            "expansion_up",
            "expansion",
            new,
        )


@pytest.mark.parametrize(
    ("change", "old", "new", "trigger"),
    [
        ("opened", None, "low", "signal_opened"),
        ("reopened", "low", "high", "signal_opened"),
        ("severity_changed", "low", "medium", "signal_opened"),
        ("severity_changed", "high", "low", None),
        ("severity_changed", None, "high", None),
        ("resolved", "high", "high", "signal_resolved"),
    ],
)
def test_signal_mapping(
    changes: MagicMock, change: str, old: str | None, new: str, trigger: str | None
) -> None:
    evidence = [] if change == "resolved" else [EVIDENCE]
    changes.signals.return_value = [
        {
            "client_id": CLIENT,
            "change": change,
            "severity": new,
            "previous_severity": old,
            "evidence_ids": evidence,
        }
    ]
    result = morning_scan.MorningScan().candidates(
        cast(RoutinesRepo, MagicMock()), workspace_id=WS, as_of=DAY
    )
    assert len(result) == (0 if trigger is None else 1)
    if result:
        assert result[0].trigger == trigger and result[0].severity == new
        assert result[0].evidence_ids == frozenset(evidence)


@pytest.mark.parametrize("days", [-1, 13, 14, 15, 29, 30, 31])
def test_deadline_crossing(changes: MagicMock, days: int) -> None:
    changes.deadlines.return_value = [{"client_id": CLIENT, "deadline": DAY + timedelta(days=days)}]
    result = morning_scan.MorningScan().candidates(
        cast(RoutinesRepo, MagicMock()), workspace_id=WS, as_of=DAY
    )
    assert [c.trigger for c in result] == (["notice_close"] if days in (14, 30) else [])


@pytest.mark.parametrize(("previous", "days"), [(None, 1), (1, 1), (4, 4), (20, 7)])
def test_since_default_and_cap(previous: int | None, days: int) -> None:
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = {
        "day": DAY - timedelta(days=previous) if previous else None
    }
    assert RoutineChangesRepo(pool).since(workspace_id=WS, as_of=DAY) == DAY - timedelta(days=days)
