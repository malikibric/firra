"""Independent fixture expectations for the T34 metric definitions."""

from datetime import date, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from firra.assessment.config import RiskState
from firra.learning.__main__ import main
from firra.learning.config import LearningConfig, load_config
from firra.learning.metrics import (
    AssessmentRow,
    Episode,
    FeedbackRow,
    OutcomeRow,
    episodes,
    precision,
    usefulness,
)
from firra.learning.report import render_markdown, report_json

A, B = UUID(int=1), UUID(int=2)
D = date(2025, 1, 1)


def test_usefulness() -> None:
    rows = [
        FeedbackRow(A, A, "risk", "fix_first", verdict, action)
        for verdict, action in [
            ("useful", "taken"),
            ("useful", "planned"),
            ("useful", None),
            ("already_known", "taken"),
            ("wrong", "planned"),
            ("not_now", "taken"),
        ]
    ]
    groups = usefulness(rows, 5)
    assert [(g.dimension, g.value) for g in groups] == [("kind", "risk"), ("play", "fix_first")]
    assert all((g.n, g.rated, g.acted, g.not_now) == (6, 5, 2, 1) for g in groups)
    assert groups[0].useful_rate == 0.6 and groups[0].wrong_rate == 0.2
    assert usefulness(rows, 6)[0].useful_rate is None
    assert usefulness(rows, 6)[0].wrong_rate is None
    assert usefulness([], 5) == []
    mixed = usefulness([*rows, FeedbackRow(B, B, "growth", "monitor", "not_now", None)], 5)
    assert [(g.dimension, g.value, g.n) for g in mixed] == [
        ("kind", "growth", 1),
        ("kind", "risk", 6),
        ("play", "fix_first", 6),
        ("play", "monitor", 1),
    ]


def test_episodes() -> None:
    states: tuple[RiskState, ...] = (
        "elevated",
        "stable",
        "elevated",
        "high",
        "high",
        "watch",
        "high",
        "elevated",
    )
    rows = [
        AssessmentRow(A, D + timedelta(days=i), risk, ("engagement", "engagement"))
        for i, risk in enumerate(states)
    ]
    rows.append(AssessmentRow(B, D, "high", ("delivery",)))
    assert episodes(list(reversed(rows))) == [
        Episode(A, D + timedelta(days=i), ("engagement",)) for i in (2, 6)
    ]
    assert episodes([]) == []


@pytest.mark.parametrize(
    ("event", "day", "as_day", "expected"),
    [
        ("churned", 0, 10, (0, 0, 1)),
        ("downgrade", 180, 180, (1, 0, 0)),
        ("renewed", 10, 10, (0, 1, 0)),
        ("expanded", 10, 10, (0, 1, 0)),
        ("unchanged", 10, 180, (0, 1, 0)),
        ("churned", 181, 180, (0, 1, 0)),
        ("churned", 20, 10, (0, 0, 1)),
    ],
)
def test_precision(event: str, day: int, as_day: int, expected: tuple[int, int, int]) -> None:
    alert = Episode(A, D, ("engagement", "delivery", "delivery"))
    outcomes = [
        OutcomeRow(A, event, D + timedelta(days=day), None),
        OutcomeRow(B, "churned", D + timedelta(days=1), None),
    ]
    groups = precision([alert], outcomes, D + timedelta(days=as_day), 180, 1)
    assert [g.family for g in groups] == ["all", "delivery", "engagement"]
    assert all((g.confirmed, g.not_confirmed, g.open) == expected for g in groups)
    assert groups[0].precision == (float(expected[0]) if not expected[2] else None)
    assert precision([alert], outcomes, D + timedelta(days=as_day), 180, 2)[0].precision is None
    assert precision([alert], outcomes, D - timedelta(days=1), 180, 1)[0].open == 0


def test_report() -> None:
    data = report_json(B, D, usefulness([], 5), precision([], [], D, 180, 5))
    text = render_markdown(data)
    assert str(A) not in text and "No observations." in text
    assert "null" in text
    rows = [FeedbackRow(A, A, "risk", "monitor", v, None) for v in ("useful", "wrong", "wrong")]
    assert "0.333" in render_markdown(report_json(B, D, usefulness(rows, 1), []))


@pytest.mark.parametrize(
    "changes",
    [
        {"extra": 1},
        {"min_rated": 0},
        {"horizon_days": -1},
        {"version": "learning.v2"},
        {"min_rated": True},
    ],
)
def test_config_validation(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LearningConfig.model_validate(
            {"version": "learning.v1", "min_rated": 5, "horizon_days": 180, **changes}
        )


def test_cli_guard(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("FIRRA_DATABASE_URL", raising=False)
    assert main(["report", "--workspace", str(A)]) == 2
    assert "required" in capsys.readouterr().err
    monkeypatch.setenv("FIRRA_DATABASE_URL", "postgresql://remote.invalid/postgres")
    assert main(["report", "--workspace", str(A)]) == 2
    assert "local" in capsys.readouterr().err
    config = load_config()
    assert config.version == "learning.v1" and config.min_rated == 5 and config.horizon_days == 180
