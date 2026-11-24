"""Pure workspace-local due checks; unknown timezones use UTC."""

from datetime import UTC, date, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from firra.routines.config import Schedule


def is_due(
    now_utc: datetime, timezone: str, schedule: Schedule, last_run_date: date | None
) -> date | None:
    zone: tzinfo
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC
    local = now_utc.astimezone(zone)
    day = local.date()
    if (
        day == last_run_date
        or local.isoweekday() not in schedule.weekdays
        or (local.hour, local.minute) < (schedule.hour, schedule.minute)
    ):
        return None
    return day
