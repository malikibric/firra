"""In-memory Microsoft identity platform + Graph (mail, calendar) for tests (no live calls)."""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid4

import httpx

from firra.connectors.microsoft.graph import graph_datetime

FOLDERS = ("inbox", "sentitems", "junkemail", "deleteditems", "drafts")
_RECEIVED = re.compile(r"receivedDateTime (ge|lt) (\S+)")


@dataclass
class FakeMicrosoft:
    messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Calendar change log: event ids in change order; deletions are logged too.
    event_log: list[str] = field(default_factory=list)
    deleted_events: set[str] = field(default_factory=set)
    expired_delta_tokens: set[str] = field(default_factory=set)
    page_size: int = 2
    revoked_refresh_tokens: set[str] = field(default_factory=set)
    rotate_refresh_token_to: str | None = None
    revoked_tenants: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    prefer_headers: list[str] = field(default_factory=list)
    subscriptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    _pages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _windows: dict[str, tuple[str, str]] = field(default_factory=dict)

    # --- data -----------------------------------------------------------------------------

    def put_event(self, event: dict[str, Any]) -> None:
        self.events[event["id"]] = event
        self.deleted_events.discard(event["id"])
        self.event_log.append(event["id"])

    def delete_event(self, event_id: str) -> None:
        self.events.pop(event_id, None)
        self.deleted_events.add(event_id)
        self.event_log.append(event_id)

    # --- transport ------------------------------------------------------------------------

    def _json(self, body: dict[str, Any], status: int = 200) -> httpx.Response:
        return httpx.Response(status, json=body)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.calls.append(f"{request.method} {url.path}")
        if url.host == "login.microsoftonline.com":
            return self._token(url.path.split("/")[1], parse_qs(request.content.decode()))
        if request.headers.get("Authorization") not in ("Bearer access-ok", "Bearer app-ok"):
            return self._json({"error": {"code": "InvalidAuthenticationToken"}}, 401)
        self.prefer_headers.append(request.headers.get("Prefer", ""))
        parts = url.path.removeprefix("/v1.0/").split("/")
        if parts[0] == "subscriptions":
            return self._subscriptions(request, parts[1] if len(parts) > 1 else None)
        rest = "/" + "/".join(parts[1:] if parts[0] == "me" else parts[2:])
        if "page" in url.params:
            return self._next_page(url.params["page"], str(url.copy_with(query=None)))
        if rest.startswith("/mailFolders/"):
            name = rest.rsplit("/", 1)[1]
            if name not in FOLDERS:
                return self._json({"error": {"code": "ErrorFolderNotFound"}}, 404)
            return self._json({"id": f"folder-{name}"})
        if rest.startswith("/messages/"):
            message_id = rest.removeprefix("/messages/")
            if message_id not in self.messages:
                return self._json({"error": {"code": "ErrorItemNotFound"}}, 404)
            return self._json(self.messages[message_id])
        if rest == "/messages":
            return self._messages(url)
        if rest == "/calendarView":
            items = self._in_window(url.params["startDateTime"], url.params["endDateTime"])
            return self._paged(str(url.copy_with(query=None)), items, delta=None)
        if rest == "/calendarView/delta":
            return self._delta(url)
        return self._json({"error": {"code": "unexpected"}}, 400)

    def _subscriptions(self, request: httpx.Request, sub_id: str | None) -> httpx.Response:
        if request.method == "GET":
            return self._json({"value": list(self.subscriptions.values())})
        if request.method == "POST":
            body = json.loads(request.content)
            for existing in self.subscriptions.values():
                if existing["resource"].lower() == body["resource"].lower():
                    return self._json({"error": {"code": "Conflict"}}, 409)
            created = {**body, "id": f"sub-{uuid4().hex[:8]}"}
            self.subscriptions[created["id"]] = created
            return self._json(created, 201)
        if sub_id not in self.subscriptions:
            return self._json({"error": {"code": "ResourceNotFound"}}, 404)
        if request.method == "DELETE":
            del self.subscriptions[sub_id]
            return httpx.Response(204)
        self.subscriptions[sub_id].update(json.loads(request.content))
        return self._json(self.subscriptions[sub_id])

    def _token(self, tenant: str, form: dict[str, list[str]]) -> httpx.Response:
        grant = form.get("grant_type", [""])[0]
        if grant == "client_credentials":
            if tenant in self.revoked_tenants:
                return self._json({"error": "unauthorized_client"}, 400)
            return self._json({"access_token": "app-ok", "expires_in": 3599})
        refresh = form.get("refresh_token", [""])[0]
        if refresh in self.revoked_refresh_tokens:
            return self._json({"error": "invalid_grant"}, 400)
        return self._json(
            {
                "access_token": "access-ok",
                "expires_in": 3599,
                "refresh_token": self.rotate_refresh_token_to or refresh,
            }
        )

    def _paged(self, base: str, items: list[dict[str, Any]], delta: str | None) -> httpx.Response:
        body: dict[str, Any] = {"value": items[: self.page_size]}
        rest = items[self.page_size :]
        if rest:
            key = str(len(self._pages))
            self._pages[key] = rest
            if delta is not None:
                self._pages[key + ":delta"] = [{"delta": delta}]
            body["@odata.nextLink"] = f"{base}?page={key}"
        elif delta is not None:
            body["@odata.deltaLink"] = f"{base}?$deltatoken={delta}"
        return self._json(body)

    def _next_page(self, key: str, base: str) -> httpx.Response:
        delta_marker = self._pages.get(key + ":delta")
        delta = str(delta_marker[0]["delta"]) if delta_marker else None
        return self._paged(base, self._pages[key], delta)

    def _messages(self, url: httpx.URL) -> httpx.Response:
        bounds = dict((op, graph_datetime(v)) for op, v in _RECEIVED.findall(url.params["$filter"]))
        assert url.params["$orderby"] == "receivedDateTime asc"
        items = sorted(
            (
                m
                for m in self.messages.values()
                if bounds["ge"] <= graph_datetime(m["receivedDateTime"])
                and ("lt" not in bounds or graph_datetime(m["receivedDateTime"]) < bounds["lt"])
            ),
            key=lambda m: str(m["receivedDateTime"]),
        )
        return self._paged(str(url.copy_with(query=None)), items, delta=None)

    def _in_window(self, start: str, end: str) -> list[dict[str, Any]]:
        lo, hi = graph_datetime(start), graph_datetime(end)
        return [
            e for e in self.events.values() if lo <= graph_datetime(e["start"]["dateTime"]) < hi
        ]

    def _delta(self, url: httpx.URL) -> httpx.Response:
        base = str(url.copy_with(query=None))
        token = url.params.get("$deltatoken")
        if token is None:
            window = (url.params["startDateTime"], url.params["endDateTime"])
            items = self._in_window(*window)
        else:
            if token in self.expired_delta_tokens:
                return self._json({"error": {"code": "SyncStateNotFound"}}, 410)
            window = self._windows[token]
            changed = dict.fromkeys(self.event_log[int(token) :])
            items = [
                {"id": i, "@removed": {"reason": "deleted"}}
                if i in self.deleted_events
                else self.events[i]
                for i in changed
            ]
        new_token = str(len(self.event_log))
        self._windows[new_token] = window
        return self._paged(base, items, delta=new_token)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def graph_address(email: str, name: str | None = None) -> dict[str, Any]:
    return {"emailAddress": {"address": email, "name": name or email.split("@")[0]}}


def graph_message(
    message_id: str,
    received: str,
    *,
    sender: str,
    to: list[str],
    body: str = "Hello, here is the update.",
    content_type: str = "text",
    folder: str = "inbox",
    is_draft: bool = False,
    conversation: str = "conv-1",
) -> dict[str, Any]:
    return {
        "id": message_id,
        "conversationId": conversation,
        "internetMessageId": f"<{message_id}@acme.com>",
        "receivedDateTime": received,
        "sentDateTime": received,
        "from": graph_address(sender),
        "toRecipients": [graph_address(a) for a in to],
        "ccRecipients": [],
        "bccRecipients": [],
        "subject": "Status update",
        "body": {"contentType": content_type, "content": body},
        "isDraft": is_draft,
        "parentFolderId": f"folder-{folder}",
        "webLink": f"https://outlook.office365.com/owa/?ItemID={message_id}",
    }


def graph_time(dt: datetime) -> dict[str, str]:
    return {"dateTime": dt.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}


def graph_event(
    event_id: str,
    start: datetime,
    end: datetime,
    attendees: list[str],
    *,
    organizer: str = "ana@northbeam.agency",
    cancelled: bool = False,
    original_start: datetime | None = None,
    all_day: bool = False,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": event_id,
        "subject": "Weekly sync",
        "isAllDay": all_day,
        "isCancelled": cancelled,
        "type": "exception" if original_start else "singleInstance",
        "start": graph_time(start),
        "end": graph_time(end),
        "organizer": graph_address(organizer),
        "attendees": [{**graph_address(a), "type": "required"} for a in attendees],
        "webLink": f"https://outlook.office365.com/calendar/item/{event_id}",
    }
    if original_start:
        data["originalStart"] = original_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    return data
