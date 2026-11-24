"""Outlook calendar over Microsoft Graph (the user's primary calendar).

Docs checked: ``calendarView`` (startDateTime/endDateTime, recurring series expanded into
occurrences) and ``calendarView/delta`` (the window is fixed by the first request and encoded in
the delta link; no $select/$filter; ``@removed`` entries for deleted events *and* for events
changed outside the window; 410 when the delta state is gone). ``Prefer: outlook.timezone="UTC"``
returns start/end in UTC. All-day events are skipped, as for Google Calendar.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from firra.connectors.microsoft.graph import GraphClient, graph_datetime
from firra_contracts import CalendarEventRecord
from firra_contracts.generated.records.calendar_event import Participant, Provider, Status

PREFER = ('outlook.timezone="UTC"', "odata.maxpagesize=100")


@dataclass(frozen=True)
class EventPage:
    events: list[CalendarEventRecord]
    removed: list[str]
    skipped: int
    next_link: str | None
    delta_link: str | None


def _participant(raw: dict[str, Any] | None) -> Participant | None:
    address = (raw or {}).get("emailAddress") or {}
    email = str(address.get("address") or "").strip().lower()
    if "@" not in email or not 3 <= len(email) <= 320:
        return None
    name = str(address.get("name") or "").strip()[:200] or None
    return Participant(email=email, name=name)


def _when(raw: dict[str, Any] | None) -> datetime | None:
    value = (raw or {}).get("dateTime")
    zone = (raw or {}).get("timeZone") or "UTC"
    if not value or zone.upper() not in ("UTC", "ETC/UTC"):
        return None  # we ask for UTC; anything else is unexpected and skipped
    return graph_datetime(str(value))


def to_record(raw: dict[str, Any]) -> CalendarEventRecord | None:
    if raw.get("isAllDay") or raw.get("type") == "seriesMaster":
        return None
    starts_at, ends_at = _when(raw.get("start")), _when(raw.get("end"))
    organizer = _participant(raw.get("organizer"))
    if starts_at is None or ends_at is None or organizer is None:
        return None
    attendees = [
        p
        for p in (_participant(a) for a in raw.get("attendees", []) if a.get("type") != "resource")
        if p is not None
    ]
    original = raw.get("originalStart")
    rescheduled = bool(
        raw.get("type") == "exception" and original and graph_datetime(str(original)) != starts_at
    )
    return CalendarEventRecord(
        provider=Provider.outlook_calendar,
        external_id=str(raw["id"])[:512],
        starts_at=starts_at,
        ends_at=ends_at,
        status=Status.cancelled if raw.get("isCancelled") else Status.confirmed,
        rescheduled=rescheduled,
        organizer=organizer,
        attendees=attendees,
        title=(str(raw.get("subject") or "")[:2000] or None),
        deep_link=(str(raw["webLink"])[:2000] if raw.get("webLink") else None),
    )


def _window_params(start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "startDateTime": start.isoformat().replace("+00:00", "Z"),
        "endDateTime": end.isoformat().replace("+00:00", "Z"),
    }


@dataclass
class OutlookCalendarClient:
    graph: GraphClient

    def _parse(
        self, items: list[dict[str, Any]]
    ) -> tuple[list[CalendarEventRecord], list[str], int]:
        events: list[CalendarEventRecord] = []
        removed: list[str] = []
        skipped = 0
        for item in items:
            if "@removed" in item:
                removed.append(str(item["id"]))
                continue
            record = to_record(item)
            if record is None:
                skipped += 1
            else:
                events.append(record)
        return events, removed, skipped

    def window(self, start: datetime, end: datetime, next_link: str | None) -> EventPage:
        page = (
            self.graph.page(next_link)
            if next_link
            else self.graph.page(self.graph.url("/calendarView"), _window_params(start, end))
        )
        events, removed, skipped = self._parse(page.items)
        return EventPage(events, removed, skipped, page.next_link, None)

    def delta(self, link: str | None, start: datetime, end: datetime) -> EventPage:
        """Follows ``link`` (a next or delta link), or starts a new round for the window."""
        page = (
            self.graph.page(link)
            if link
            else self.graph.page(self.graph.url("/calendarView/delta"), _window_params(start, end))
        )
        events, removed, skipped = self._parse(page.items)
        return EventPage(events, removed, skipped, page.next_link, page.delta_link)
