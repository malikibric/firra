"""Google Calendar API (v3) over HTTPS for the user's primary calendar.

Docs checked: events.list (singleEvents=true expands recurrences, showDeleted=true returns
cancellations, timeMin/timeMax, pageToken, nextSyncToken on the last page, syncToken for changes,
410 Gone when a sync token expires). All-day events (date without dateTime) are skipped.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from firra.connectors.google import limits
from firra.http import ProviderError, request_with_retry
from firra_contracts import CalendarEventRecord
from firra_contracts.generated.records.calendar_event import Participant, Provider, Status

API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


class SyncTokenExpiredError(Exception):
    """The stored sync token is no longer valid; do a fresh initial sync."""


@dataclass(frozen=True)
class EventPage:
    events: list[CalendarEventRecord]
    skipped: int
    next_page_token: str | None
    next_sync_token: str | None


def _participant(raw: dict[str, Any]) -> Participant | None:
    email = str(raw.get("email") or "").strip().lower()
    if "@" not in email or raw.get("resource"):
        return None
    name = str(raw.get("displayName") or "").strip()[:200] or None
    return Participant(email=email, name=name)


def to_record(raw: dict[str, Any]) -> CalendarEventRecord | None:
    start, end = raw.get("start") or {}, raw.get("end") or {}
    if "dateTime" not in start or "dateTime" not in end:
        return None  # all-day or incomplete (cancelled instances may lack times)
    organizer = _participant(raw.get("organizer") or {}) or _participant(raw.get("creator") or {})
    if organizer is None:
        return None
    attendees = [p for p in (_participant(a) for a in raw.get("attendees", [])) if p is not None]
    original = (raw.get("originalStartTime") or {}).get("dateTime")
    return CalendarEventRecord(
        provider=Provider.google_calendar,
        external_id=str(raw["id"])[:512],
        starts_at=datetime.fromisoformat(start["dateTime"]),
        ends_at=datetime.fromisoformat(end["dateTime"]),
        status=Status(raw.get("status", "confirmed")),
        rescheduled=bool(original and original != start["dateTime"]),
        organizer=organizer,
        attendees=attendees,
        title=(str(raw.get("summary") or "")[:2000] or None),
        deep_link=raw.get("htmlLink"),
    )


@dataclass
class CalendarClient:
    client: httpx.Client
    access_token: str

    def _page(self, params: dict[str, Any]) -> EventPage:
        response = request_with_retry(
            self.client,
            "GET",
            API,
            params=params,
            headers={"Authorization": f"Bearer {self.access_token}"},
            attempts=limits.ATTEMPTS,
            retry_if=limits.rate_limited,
        )
        if response.status_code == 410:
            raise SyncTokenExpiredError
        if response.status_code >= 400:
            raise ProviderError.from_response(response)
        body: dict[str, Any] = response.json()
        events: list[CalendarEventRecord] = []
        skipped = 0
        for item in body.get("items", []):
            record = to_record(item)
            if record is None:
                skipped += 1
            else:
                events.append(record)
        return EventPage(
            events=events,
            skipped=skipped,
            next_page_token=body.get("nextPageToken"),
            next_sync_token=body.get("nextSyncToken"),
        )

    def window(self, start: datetime, end: datetime, page_token: str | None) -> EventPage:
        params: dict[str, Any] = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "singleEvents": "true",
            "showDeleted": "true",
            "maxResults": 250,
        }
        if page_token:
            params["pageToken"] = page_token
        return self._page(params)

    def changes(self, sync_token: str | None, page_token: str | None, since: datetime) -> EventPage:
        """With a sync token: changes since then. Without: upcoming events from ``since``."""
        params: dict[str, Any] = {"singleEvents": "true", "showDeleted": "true", "maxResults": 250}
        if sync_token:
            params["syncToken"] = sync_token
        else:
            params["timeMin"] = since.isoformat()
        if page_token:
            params["pageToken"] = page_token
        return self._page(params)
