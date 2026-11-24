"""Fixture oracles for lead time and the aggregate report; no database or network."""

import json
from contextlib import nullcontext
from dataclasses import FrozenInstanceError, fields
from datetime import date, timedelta
from uuid import UUID

import pytest

from firra.learning import __main__ as cli
from firra.learning.metrics import (
    AssessmentRow,
    Episode,
    FeedbackRow,
    OutcomeRow,
    lead_time,
)
from firra.learning.report import render_markdown, report_json

A, B = UUID(int=1), UUID(int=2)
END = date(2026, 10, 3)


@pytest.mark.parametrize(("days", "detected"), [(0, 0), (365, 1), (366, 0)])
def test_window_boundaries(days: int, detected: int) -> None:
    result = lead_time(
        [Episode(A, END - timedelta(days=days), ())], [OutcomeRow(A, "churned", END, None)]
    )
    assert (result.detected, result.missed) == (detected, 1 - detected)
    assert result.median_lead_days == (days if detected else None)


def test_notice_earliest_episode_and_client_isolation() -> None:
    alerts = [Episode(A, END - timedelta(days=i), ("engagement",)) for i in (10, 50)]
    alerts.append(Episode(B, END - timedelta(days=100), ("delivery",)))
    notice = END - timedelta(days=20)
    result = lead_time(alerts, [OutcomeRow(A, "downgrade", END, notice)])
    assert (result.detected, result.median_lead_days, result.share_ge_30d) == (1, 30, 1)
    assert lead_time(alerts, [OutcomeRow(A, "churned", END, None)]).median_lead_days == 50
    assert lead_time(alerts[2:], [OutcomeRow(A, "churned", END, None)]).missed == 1
    assert lead_time([], [OutcomeRow(A, "churned", END, None)]).missed == 1


@pytest.mark.parametrize("event", ["price_increase", "referral", "renewed", "expanded"])
def test_ignored_outcomes(event: str) -> None:
    result = lead_time(
        [Episode(A, END - timedelta(days=40), ("delivery",))], [OutcomeRow(A, event, END, None)]
    )
    assert (
        result.detected,
        result.missed,
        result.recall,
        result.median_lead_days,
        result.share_ge_30d,
        result.families,
    ) == (0, 0, None, None, None, ())


def test_separate_outcomes_lower_medians_and_distinct_families() -> None:
    alerts = [Episode(A, END - timedelta(days=50), ("engagement", "delivery", "delivery"))]
    outcomes = [
        OutcomeRow(A, "churned", END, None),
        OutcomeRow(A, "downgrade", END - timedelta(days=40), None),
        OutcomeRow(B, "churned", END, None),
    ]
    result = lead_time(alerts, outcomes)
    assert (result.detected, result.missed, result.recall) == (2, 1, 2 / 3)
    assert (result.median_lead_days, result.share_ge_30d) == (10, 0.5)
    assert [f.family for f in result.families] == ["delivery", "engagement"]
    assert all(
        (f.detected, f.median_lead_days, f.share_ge_30d) == (2, 10, 0.5) for f in result.families
    )
    outcomes.append(OutcomeRow(A, "churned", END - timedelta(days=20), None))
    assert lead_time(alerts, outcomes).median_lead_days == 30
    assert all(f.median_lead_days == 30 for f in lead_time(alerts, outcomes).families)
    assert lead_time(list(reversed(alerts)), list(reversed(outcomes))) == lead_time(
        alerts, outcomes
    )
    with pytest.raises(FrozenInstanceError):
        setattr(result, fields(result)[0].name, 0)


def test_miss_has_no_family_and_no_detected_denominator() -> None:
    result = lead_time([Episode(B, END, ("delivery",))], [OutcomeRow(A, "churned", END, None)])
    assert (result.detected, result.missed, result.recall) == (0, 1, 0)
    assert result.median_lead_days is None and result.share_ge_30d is None
    assert result.families == ()


def test_report_json_and_markdown() -> None:
    result = lead_time(
        [Episode(A, END - timedelta(days=30), ("delivery",))],
        [
            OutcomeRow(A, "churned", END, None),
            OutcomeRow(B, "churned", END, None),
            OutcomeRow(B, "downgrade", END, None),
        ],
    )
    report = report_json(B, END, [], [], result)
    decoded = json.loads(json.dumps(report))["lead_time"]
    assert decoded["detected"] == 1 and decoded["missed"] == 2
    assert set(decoded["families"][0]) == {"family", "detected", "median_lead_days", "share_ge_30d"}
    text = render_markdown(report)
    assert text.count("## Lead time") == 1
    assert "| all | 1 | 2 | 0.333 | 30 | 1.000 |" in text
    assert "| delivery | 1 | null | null | 30 | 1.000 |" in text
    assert str(A) not in text
    assert "| all | 0 | 0 | null | null | null |" in render_markdown(report_json(B, END, [], []))


def test_cli_uses_existing_inputs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FakeRepo:
        def __init__(self, pool: object) -> None:
            pass

        def feedback(self, *, workspace_id: UUID, as_of: date) -> list[FeedbackRow]:
            return []

        def assessments(self, *, workspace_id: UUID, as_of: date) -> list[AssessmentRow]:
            assert workspace_id == A and as_of == END
            return [
                AssessmentRow(A, END - timedelta(days=d), "high", ("delivery",)) for d in (30, 29)
            ]

        def outcomes(self, *, workspace_id: UUID, as_of: date) -> list[OutcomeRow]:
            return [OutcomeRow(A, "churned", END, None)]

    monkeypatch.setenv("FIRRA_DATABASE_URL", "postgresql://localhost/postgres")
    monkeypatch.setattr(cli, "create_pool_for", lambda _: nullcontext(None))
    monkeypatch.setattr(cli, "LearningRepo", FakeRepo)
    args = ["report", "--workspace", str(A), "--as-of", END.isoformat()]
    assert cli.main([*args, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["lead_time"]["median_lead_days"] == 30
    assert cli.main(args) == 0
    assert "## Lead time" in capsys.readouterr().out
