"""Working hours between two instants: Monday to Friday, 09:00 to 18:00 in a time zone.

Chat replies are measured in working hours so that an evening message answered the next morning is
not slow (spec section 3). Local time zone rules (daylight saving) apply per day.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY_START = time(9, 0)
DAY_END = time(18, 0)


def working_hours_between(start: datetime, end: datetime, timezone: str) -> float:
    """Hours of Monday-Friday 09:00-18:00 (local) inside ``[start, end]``; 0 when end <= start."""
    if end <= start:
        return 0.0
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    first = start.astimezone(zone).date()
    last = end.astimezone(zone).date()
    total = 0.0
    day = first
    while day <= last:
        if day.weekday() < 5:
            open_at = datetime.combine(day, DAY_START, tzinfo=zone).astimezone(UTC)
            close_at = datetime.combine(day, DAY_END, tzinfo=zone).astimezone(UTC)
            overlap = min(end, close_at) - max(start, open_at)
            if overlap > timedelta(0):
                total += overlap.total_seconds() / 3600
        day += timedelta(days=1)
    return total
