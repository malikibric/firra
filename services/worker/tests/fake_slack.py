"""Hand-written synthetic Slack server; never talks to Slack."""

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from firra.connectors.slack.auth import READ_SCOPES


class FakeSlack:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.scopes = list(READ_SCOPES)
        self.error: str | None = None
        self.throttle: str | None = None
        self.users = {
            "UOWNER": {
                "id": "UOWNER",
                "team_id": "TOWN",
                "real_name": "Synthetic Owner",
                "profile": {"email": "owner@firm.example"},
            },
            "UCOLLEAGUE": {
                "id": "UCOLLEAGUE",
                "team_id": "TOWN",
                "real_name": "Synthetic Colleague",
                "profile": {"email": "colleague@firm.example"},
            },
            "UCLIENT": {
                "id": "UCLIENT",
                "team_id": "TCLIENT",
                "real_name": "Synthetic Client",
                "profile": {"email": "client@acme.example"},
            },
            "UNOEMAIL": {
                "id": "UNOEMAIL",
                "team_id": "TCLIENT",
                "real_name": "Synthetic External",
                "profile": {},
            },
        }
        self.channels = {
            "CMAPPED": {"id": "CMAPPED", "name": "acme-project", "is_ext_shared": True},
            "CUNMAPPED": {"id": "CUNMAPPED", "name": "private-unmapped", "is_ext_shared": True},
            "CINTERNAL": {"id": "CINTERNAL", "name": "acme-internal", "is_ext_shared": False},
            "DEXTERNAL": {"id": "DEXTERNAL", "is_im": True, "user": "UCLIENT"},
            "DCOLLEAGUE": {"id": "DCOLLEAGUE", "is_im": True, "user": "UCOLLEAGUE"},
        }
        self.messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.threads: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.members = {
            "CMAPPED": ["UOWNER", "UCLIENT", "UNOEMAIL"],
            "CUNMAPPED": ["UOWNER", "UCLIENT"],
            "CINTERNAL": ["UOWNER", "UCOLLEAGUE"],
        }
        for message in json.loads(
            (Path(__file__).parent / "fixtures/slack/messages.json").read_text()
        ):
            self.add(
                message["channel"], message["text"], days=message["days"], user=message["user"]
            )

    def add(self, channel: str, text: str, *, days: int, user: str) -> dict[str, Any]:
        ts = f"{(self.now - timedelta(days=days)).timestamp():.6f}"
        message = {"type": "message", "ts": ts, "user": user, "text": text}
        self.messages[channel].append(message)
        return message

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        params = dict(request.url.params)
        self.calls.append((method, params))
        if (
            getattr(self, "throttle_second_page", False)
            and method == "conversations.history"
            and params.get("cursor") == "2"
        ):
            self.throttle_second_page = False
            self.throttle = method
        if self.throttle == method:
            self.throttle = None
            return httpx.Response(
                429, headers={"Retry-After": "120"}, json={"ok": False, "error": "ratelimited"}
            )
        if self.error:
            return httpx.Response(200, json={"ok": False, "error": self.error})
        headers = {"x-oauth-scopes": ",".join(self.scopes)}
        if method == "auth.test":
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "ok": True,
                    "team_id": "TOWN",
                    "user_id": "UOWNER",
                    "team": "Synthetic Workspace",
                    "url": "https://synthetic.slack.com/",
                },
            )
        if method == "auth.revoke":
            return httpx.Response(200, json={"ok": True, "revoked": True})
        if method == "chat.getPermalink":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "permalink": (
                        f"https://synthetic.slack.com/archives/{params['channel']}/"
                        f"p{params['message_ts'].replace('.', '')}"
                    ),
                },
            )
        if method == "users.info":
            return httpx.Response(200, json={"ok": True, "user": self.users[params["user"]]})
        if method == "conversations.members":
            return httpx.Response(
                200, json={"ok": True, "members": self.members.get(params["channel"], [])}
            )
        values: list[dict[str, Any]]
        field = "messages"
        if method == "users.list":
            field = "members"
            values = list(self.users.values())
        elif method == "conversations.list":
            field = "channels"
            values = list(self.channels.values())
        elif method == "conversations.history":
            values = [
                message
                for message in self.messages[params["channel"]]
                if float(params.get("oldest", "0"))
                <= float(message["ts"])
                <= float(params.get("latest", "inf"))
            ]
            values = sorted(values, key=lambda message: message["ts"], reverse=True)
        elif method == "conversations.replies":
            values = self.threads.get((params["channel"], params["ts"]), [])
        else:
            raise AssertionError("unexpected synthetic API method")
        offset = int(params.get("cursor", "0"))
        following = str(offset + 2) if len(values) > offset + 2 else ""
        return httpx.Response(
            200,
            json={
                "ok": True,
                field: values[offset : offset + 2],
                "has_more": bool(following),
                "response_metadata": {"next_cursor": following},
            },
        )
