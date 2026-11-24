from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from firra.routines.config import RoutinesConfig, Schedule, load_config
from firra.routines.model import Decision
from firra.routines.schedule import is_due
from firra.routines.words import WORDS, render


@pytest.mark.parametrize("weekday", range(1, 8))
def test_weekdays_hour_and_previous_run(weekday: int) -> None:
    day = date(2026, 10, 5) + timedelta(days=weekday - 1)
    schedule = Schedule(hour=9, minute=30, weekdays=(weekday,))
    before = datetime.combine(day, datetime.min.time(), UTC).replace(hour=9, minute=29)
    after = before + timedelta(minutes=1)
    assert is_due(before, "UTC", schedule, None) is None
    assert is_due(after, "UTC", schedule, None) == day
    assert is_due(after, "UTC", schedule, day) is None
    assert is_due(after, "UTC", schedule, day - timedelta(days=1)) == day
    assert is_due(after + timedelta(days=1), "UTC", schedule, None) is None


@pytest.mark.parametrize(
    ("zone", "instant", "hour"),
    [
        ("Europe/Berlin", "2026-03-29T01:00:00+00:00", 3),
        ("Europe/Berlin", "2026-10-25T01:00:00+00:00", 2),
        ("America/New_York", "2026-03-08T07:00:00+00:00", 3),
        ("America/New_York", "2026-11-01T06:00:00+00:00", 1),
    ],
)
def test_dst(zone: str, instant: str, hour: int) -> None:
    now = datetime.fromisoformat(instant)
    schedule = Schedule(hour=hour, minute=0, weekdays=(7,))
    assert is_due(now, zone, schedule, None) == now.date()
    assert is_due(now, zone, schedule, now.date()) is None


def test_unknown_timezone_uses_utc() -> None:
    now = datetime(2026, 10, 5, 9, tzinfo=UTC)
    assert (
        is_due(now, "unknown/zone", Schedule(hour=9, minute=0, weekdays=(1,)), None) == now.date()
    )


@pytest.mark.parametrize("reason", WORDS)
@pytest.mark.parametrize("evidence", [False, True])
def test_fixed_words_and_fact_basis(reason: str, evidence: bool) -> None:
    decision = Decision(
        UUID(int=1),
        "state_change",
        reason,
        "attention",
        None,
        ("PRIVATE INPUT",),
        frozenset({UUID(int=2)}) if evidence else frozenset(),
        2,
        None,
        date(2026, 10, 5),
    )
    text = render(decision)
    assert len(WORDS) == 14
    for value, limit in [
        (text.title, 120),
        (text.body, 2000),
        (text.interpretation, 600),
        (text.recommendation, 600),
    ]:
        assert 0 < len(value) <= limit
        assert "PRIVATE INPUT" not in value
    assert text.facts == [
        {"id": reason, "text": text.interpretation, "basis": "evidence" if evidence else "metadata"}
    ]
    assert render(replace(decision, also=())) == text


def test_notice_thresholds_order() -> None:
    raw = load_config().model_dump()
    raw.update(notice_close_days=10, notice_near_days=10)
    assert RoutinesConfig.model_validate(raw).notice_near_days == 10
    raw["notice_near_days"] = 9
    with pytest.raises(ValidationError, match="notice_near_days"):
        RoutinesConfig.model_validate(raw)
