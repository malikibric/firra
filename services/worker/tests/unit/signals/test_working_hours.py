"""working_hours_between: Monday-Friday 09:00-18:00 in the workspace time zone."""

from datetime import UTC, datetime

import pytest

from firra.signals.working_hours import working_hours_between


def utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_inside_one_working_day() -> None:
    assert working_hours_between(utc(2026, 9, 29, 10), utc(2026, 9, 29, 14, 30), "UTC") == 4.5


def test_outside_hours_do_not_count() -> None:
    # Tuesday 17:00 to Wednesday 10:00: one hour before 18:00 plus one hour after 09:00
    assert working_hours_between(utc(2026, 9, 29, 17), utc(2026, 9, 30, 10), "UTC") == 2.0


def test_friday_evening_to_monday_morning() -> None:
    assert working_hours_between(utc(2026, 9, 25, 17, 45), utc(2026, 9, 28, 9, 15), "UTC") == 0.5


def test_weekend_only_is_zero() -> None:
    assert working_hours_between(utc(2026, 9, 26, 9), utc(2026, 9, 27, 18), "UTC") == 0.0


def test_end_before_start_is_zero() -> None:
    assert working_hours_between(utc(2026, 9, 29, 12), utc(2026, 9, 29, 10), "UTC") == 0.0


def test_time_zone_shifts_the_window() -> None:
    # 07:00-10:00 UTC is 09:00-12:00 in Berlin (CEST): three working hours there, one in UTC
    assert working_hours_between(utc(2026, 9, 29, 7), utc(2026, 9, 29, 10), "Europe/Berlin") == 3.0
    assert working_hours_between(utc(2026, 9, 29, 7), utc(2026, 9, 29, 10), "UTC") == 1.0


def test_daylight_saving_change_uses_local_days() -> None:
    # Europe/Berlin leaves summer time on 2026-10-25 (a Sunday); Monday 09:00 CET is 08:00 UTC
    assert (
        working_hours_between(utc(2026, 10, 26, 7), utc(2026, 10, 26, 10), "Europe/Berlin") == 2.0
    )


@pytest.mark.parametrize("zone", ["Nowhere/Land", ""])
def test_unknown_zone_falls_back_to_utc(zone: str) -> None:
    assert working_hours_between(utc(2026, 9, 29, 10), utc(2026, 9, 29, 11), zone) == 1.0
