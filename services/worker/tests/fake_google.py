"""In-memory Gmail + Google OAuth token endpoint for tests (no live calls, CLAUDE.md § 14)."""

import base64
import json
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs

import httpx


@dataclass
class FakeMessage:
    raw: bytes
    thread_id: str
    labels: list[str] = field(default_factory=lambda: ["INBOX"])

    @property
    def epoch(self) -> int:
        for line in self.raw.decode(errors="replace").splitlines():
            if line.lower().startswith("date:"):
                return int(parsedate_to_datetime(line[5:].strip()).timestamp())
        return 0


@dataclass
class FakeGoogle:
    messages: dict[str, FakeMessage] = field(default_factory=dict)
    history_id: int = 100
    history: list[dict[str, Any]] = field(default_factory=list)
    page_size: int = 2
    revoked_refresh_tokens: set[str] = field(default_factory=set)
    refresh_rotations: dict[str, str] = field(default_factory=dict)
    token_expires_in: int = 3600
    calls: list[str] = field(default_factory=list)
    list_queries: list[str] = field(default_factory=list)
    # Calendar: events by id, a change log, and the sequence number each sync token stands for.
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    event_log: list[str] = field(default_factory=list)
    expired_sync_tokens: set[str] = field(default_factory=set)
    watch_topics: list[str] = field(default_factory=list)
    # The next N Gmail/Calendar API calls are throttled (403 rateLimitExceeded, as Gmail does).
    throttle: int = 0
    # The ``format`` of every messages.get call, in order.
    formats: list[str] = field(default_factory=list)

    def put_event(self, event: dict[str, Any]) -> None:
        self.events[event["id"]] = event
        self.event_log.append(event["id"])

    def add(
        self,
        message_id: str,
        raw: bytes,
        *,
        thread_id: str = "t",
        labels: list[str] | None = None,
        record_history: bool = False,
    ) -> None:
        self.messages[message_id] = FakeMessage(raw, thread_id, labels or ["INBOX"])
        if record_history:
            self.history_id += 1
            self.history.append(
                {"id": str(self.history_id), "messagesAdded": [{"message": {"id": message_id}}]}
            )

    def delete(self, message_id: str) -> None:
        self.messages.pop(message_id, None)
        self.history_id += 1
        self.history.append(
            {"id": str(self.history_id), "messagesDeleted": [{"message": {"id": message_id}}]}
        )

    def _json(self, body: dict[str, Any], status: int = 200) -> httpx.Response:
        return httpx.Response(status, json=body)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.calls.append(f"{request.method} {url.path}")
        if url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            if form.get("refresh_token", [""])[0] in self.revoked_refresh_tokens:
                return self._json({"error": "invalid_grant"}, 400)
            token_body: dict[str, Any] = {
                "access_token": "access-ok",
                "expires_in": self.token_expires_in,
            }
            rotated = self.refresh_rotations.get(form.get("refresh_token", [""])[0])
            if rotated is not None:
                token_body["refresh_token"] = rotated
            return self._json(token_body)
        if request.headers.get("Authorization") != "Bearer access-ok":
            return self._json({"error": "unauthorized"}, 401)
        if self.throttle > 0:
            self.throttle -= 1
            error = {"code": 403, "errors": [{"reason": "rateLimitExceeded"}]}
            return self._json({"error": error}, 403)
        if url.path == "/calendar/v3/calendars/primary/events":
            return self._events(url.params)
        path = url.path.removeprefix("/gmail/v1/users/me")
        if path == "/watch" and request.method == "POST":
            self.watch_topics.append(json.loads(request.content)["topicName"])
            return self._json({"historyId": str(self.history_id), "expiration": "1790000000000"})
        if path == "/profile":
            return self._json({"historyId": str(self.history_id)})
        if path == "/messages":
            return self._list(url.params)
        if path.startswith("/messages/"):
            message = self.messages.get(path.rsplit("/", 1)[1])
            if message is None:
                return self._json({"error": "not found"}, 404)
            body: dict[str, Any] = {
                "id": path.rsplit("/", 1)[1],
                "threadId": message.thread_id,
                "labelIds": message.labels,
            }
            self.formats.append(url.params.get("format", "full"))
            if url.params.get("format") == "metadata":
                wanted = {h.lower() for h in url.params.get_list("metadataHeaders")}
                parsed = BytesParser(policy=policy.default).parsebytes(message.raw)
                body["payload"] = {
                    "headers": [
                        {"name": name, "value": str(value)}
                        for name, value in parsed.items()
                        if name.lower() in wanted
                    ]
                }
            else:
                body["raw"] = base64.urlsafe_b64encode(message.raw).rstrip(b"=").decode()
            return self._json(body)
        if path == "/history":
            start = int(url.params["startHistoryId"])
            if start < 50:
                return self._json({"error": "history too old"}, 404)
            entries = [h for h in self.history if int(h["id"]) > start]
            return self._json({"history": entries, "historyId": str(self.history_id)})
        return self._json({"error": "unexpected"}, 400)

    def _list(self, params: httpx.QueryParams) -> httpx.Response:
        self.list_queries.append(params["q"])
        terms = dict(
            t.split(":", 1) for t in params["q"].split() if t.startswith(("after:", "before:"))
        )
        after, before = int(terms.get("after", 0)), int(terms.get("before", 2**40))
        ids = sorted(i for i, m in self.messages.items() if after <= m.epoch < before)
        offset = int(params.get("pageToken", 0))
        page = ids[offset : offset + self.page_size]
        body: dict[str, Any] = {
            "messages": [{"id": i, "threadId": self.messages[i].thread_id} for i in page]
        }
        if offset + self.page_size < len(ids):
            body["nextPageToken"] = str(offset + self.page_size)
        return self._json(body)

    def _events(self, params: httpx.QueryParams) -> httpx.Response:
        token = params.get("syncToken")
        if token in self.expired_sync_tokens:
            return self._json({"error": "gone"}, 410)
        if token is not None:
            changed = dict.fromkeys(self.event_log[int(token) :])
            items = [self.events[i] for i in changed]
        else:
            lo = params.get("timeMin", "0000")
            hi = params.get("timeMax", "9999")
            items = [
                e
                for e in self.events.values()
                if lo
                <= (e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", ""))
                < hi
            ]
        offset = int(params.get("pageToken", 0))
        page = items[offset : offset + self.page_size]
        body: dict[str, Any] = {"items": page}
        if offset + self.page_size < len(items):
            body["nextPageToken"] = str(offset + self.page_size)
        else:
            body["nextSyncToken"] = str(len(self.event_log))
        return self._json(body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def stored_tokens(refresh_token: str) -> str:
    return json.dumps({"refresh_token": refresh_token})


def event(
    event_id: str,
    start: str,
    end: str,
    attendees: list[str],
    *,
    organizer: str = "ana@northbeam.agency",
    status: str = "confirmed",
    original_start: str | None = None,
    summary: str = "Weekly sync",
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": event_id,
        "status": status,
        "summary": summary,
        "htmlLink": f"https://calendar.google.com/event?eid={event_id}",
        "organizer": {"email": organizer},
        "attendees": [{"email": a} for a in attendees],
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }
    if original_start:
        data["originalStartTime"] = {"dateTime": original_start}
    return data
