"""Runs every case of schedule_oracle.yaml (T43e) through `firra.routines.schedule.is_due`.

Written from the spec in the original task spec and the IANA rules, not from T43d's
implementation. Expectations are never edited to make a function pass; a disagreement goes to the
lead and either the spec or the code changes.
"""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from firra.routines.config import Schedule
from firra.routines.schedule import is_due

ORACLE: dict[str, list[dict[str, Any]]] = yaml.safe_load(
    (Path(__file__).parent / "schedule_oracle.yaml").read_text()
)


def on(value: Any) -> date | None:
    # PyYAML parses an ISO `YYYY-MM-DD` into a `date`; accept both so the schema stays readable.
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(value)


def cases() -> list[Any]:
    return [pytest.param(case, id=case["id"]) for case in ORACLE["is_due"]]


@pytest.mark.parametrize("case", cases())
def test_is_due(case: dict[str, Any]) -> None:
    raw = case["input"]
    schedule = Schedule(
        hour=raw["schedule"]["hour"],
        minute=raw["schedule"]["minute"],
        weekdays=tuple(raw["schedule"]["weekdays"]),
    )
    now_utc = datetime.fromisoformat(raw["now_utc"])
    assert now_utc.tzinfo is not None
    assert now_utc.utcoffset() == timedelta(0)  # the contract takes aware UTC

    result = is_due(now_utc, raw["timezone"], schedule, on(raw.get("last_run_date")))
    expected = on(case["expect"])
    if expected is None:
        assert result is None
    else:
        assert result is not None
        # Requirement 7: a date, never a datetime.
        assert isinstance(result, date) and not isinstance(result, datetime)
        assert result == expected


def test_oracle_cases_are_well_formed() -> None:
    assert set(ORACLE) == {"is_due"}
    for case in ORACLE["is_due"]:
        assert set(case) == {"id", "why", "input", "expect"}, case.get("id")
        assert isinstance(case["id"], str) and case["id"]
        assert isinstance(case["why"], str) and case["why"]
        assert isinstance(case["input"]["timezone"], str)
