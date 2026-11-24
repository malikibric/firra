"""Shared scaffolding for the sync oracles (L2-05): probes, worlds, fakes.

One probe mechanism: ``implementation_present(*modules)`` skips a file until
the named implementation modules exist. The L2-02/L2S-04 briefs name the new
modules; the tests below never import Codex's in-flight branch.
"""
# ruff: noqa: E501  (long SQL and mail fixtures, like the route twin tests)

import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row


def implementation_present(*modules: str) -> bool:
    """True only when every named implementation module imports."""
    return all(importlib.util.find_spec(name) is not None for name in modules)


def google_sync_implemented() -> bool:
    """L2-02: local token plumbing for Google sync."""
    return implementation_present("firra.connectors.google.local_tokens")


def slack_sync_implemented() -> bool:
    """L2S-04: the Slack connector sync modules."""
    return implementation_present("firra.connectors.slack.sync")


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "oracle_sync"


class CapturingQueue:
    def __init__(self) -> None:
        self.envelopes: list[Any] = []
        self.delays: list[float] = []

    def enqueue(self, envelope: Any, *, delay_seconds: float = 0) -> None:
        self.envelopes.append(envelope)
        self.delays.append(delay_seconds)


class FakeSlack:
    """Minimal Slack Web API behind httpx.MockTransport (synthetic data only).

    Universe: users, channels and messages loaded from dicts (see the
    fixtures under tests/fixtures/oracle_sync/). Modes switch auth and
    throttling behaviour per test.
    """

    def __init__(self, team_id: str = "T-OWN") -> None:
        self.team_id = team_id
        self.users: dict[str, dict[str, Any]] = {}
        self.channels: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.auth_mode = "ok"
        self.throttle_remaining = 0
        self.calls: list[str] = []
        self.bearers: list[str] = []

    def add_user(
        self, user_id: str, name: str, email: str, team: str | None = None, bot: bool = False
    ) -> None:
        self.users[user_id] = {
            "id": user_id,
            "name": name,
            "email": email,
            "team": team or self.team_id,
            "bot": bot,
            "is_bot": bot,
            "profile": {"display_name": name, "email": email},
        }

    def add_channel(
        self, channel_id: str, name: str, kind: str = "public", teams: list[str] | None = None
    ) -> None:
        self.channels[channel_id] = {
            "id": channel_id,
            "name": name,
            "kind": kind,
            "teams": teams or [self.team_id],
            "is_ext_shared": len(teams or []) > 1,
            "is_im": kind == "im",
            "is_private": kind == "private",
            "user": "U-EXT" if channel_id == "D-client" else "U-COL",
        }

    def add_message(
        self,
        channel_id: str,
        ts: str,
        user_id: str,
        text: str,
        thread_ts: str | None = None,
        subtype: str | None = None,
    ) -> None:
        message: dict[str, Any] = {"ts": ts, "user": user_id, "text": text}
        if thread_ts is not None:
            message["thread_ts"] = thread_ts
            for parent in self.messages.get(channel_id, []):
                if parent["ts"] == thread_ts:
                    parent["thread_ts"] = thread_ts
                    parent["reply_count"] = int(parent.get("reply_count", 0)) + 1
        if subtype is not None:
            message["subtype"] = subtype
        self.messages.setdefault(channel_id, []).append(message)

    def thread_parent_ts(self, channel_id: str) -> str | None:
        seen: dict[str, None] = {}
        for message in self.messages.get(channel_id, []):
            seen.setdefault(str(message.get("thread_ts") or message["ts"]))
        for key in seen:
            if any(m.get("thread_ts") == key for m in self.messages.get(channel_id, [])):
                return key
        return None

    def latest_reply(self, channel_id: str, parent_ts: str) -> str:
        replies = [
            str(m["ts"])
            for m in self.messages.get(channel_id, [])
            if m.get("thread_ts") == parent_ts
        ]
        return str(max([parent_ts, *replies]))

    def _json(self, body: dict[str, Any], status: int = 200) -> httpx.Response:
        return httpx.Response(status, json=body)

    def _authed(self, request: httpx.Request) -> httpx.Response | None:
        header = request.headers.get("Authorization", "")
        self.bearers.append(header)
        if self.auth_mode == "invalid_auth":
            return self._json({"ok": False, "error": "invalid_auth"})
        if self.auth_mode == "missing_scope":
            return self._json({"ok": False, "error": "missing_scope"})
        if not header.startswith("Bearer "):
            return self._json({"ok": False, "error": "invalid_auth"})
        return None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}")
        denied = self._authed(request)
        if denied is not None:
            return denied
        if self.throttle_remaining > 0 and request.url.path.endswith(
            ("conversations.history", "conversations.replies")
        ):
            self.throttle_remaining -= 1
            return httpx.Response(
                429, headers={"Retry-After": "1"}, json={"ok": False, "error": "ratelimited"}
            )
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = {}
        params = dict(request.url.params)
        merged = {**params, **body}
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "auth.test":
            return self._json(
                {
                    "ok": True,
                    "url": "https://oracle.slack.com/",
                    "team": "Oracle",
                    "user": "owner",
                    "team_id": self.team_id,
                    "user_id": "U-OWNER",
                }
            )
        if method == "auth.revoke":
            return self._json({"ok": True, "revoked": True})
        if method == "users.list":
            return self._json({"ok": True, "members": list(self.users.values())})
        if method == "users.info":
            user = self.users.get(str(merged.get("user", "")), {})
            return self._json({"ok": True, "user": user})
        if method == "conversations.list":
            return self._json({"ok": True, "channels": list(self.channels.values())})
        if method == "conversations.history":
            return self._history(str(merged.get("channel", "")), merged)
        if method == "conversations.replies":
            return self._replies(str(merged.get("channel", "")), str(merged.get("ts", "")), merged)
        if method == "chat.getPermalink":
            channel, ts = str(merged.get("channel", "")), str(merged.get("message_ts", ""))
            return self._json(
                {
                    "ok": True,
                    "channel": channel,
                    "permalink": f"https://oracle.slack.com/archives/{channel}/p{ts.replace('.', '')}",
                }
            )
        return self._json({"ok": False, "error": "unknown_method"})

    def _page(
        self, items: list[dict[str, Any]], merged: dict[str, Any], limit: int = 100
    ) -> httpx.Response:
        cursor = int(merged.get("cursor", 0) or 0)
        page = items[cursor : cursor + limit]
        body: dict[str, Any] = {"ok": True, "messages": page, "has_more": False}
        if cursor + limit < len(items):
            body["has_more"] = True
            body["response_metadata"] = {"next_cursor": str(cursor + limit)}
        return self._json(body)

    def _history(self, channel_id: str, merged: dict[str, Any]) -> httpx.Response:
        items = [
            {**m, "latest_reply": self.latest_reply(channel_id, m["ts"])}
            if m.get("thread_ts", m["ts"]) == m["ts"]
            and self.latest_reply(channel_id, m["ts"]) != m["ts"]
            else m
            for m in sorted(self.messages.get(channel_id, []), key=lambda m: m["ts"])
        ]
        items = [
            m
            for m in items
            if float(merged.get("oldest", 0))
            <= float(m["ts"])
            <= float(merged.get("latest", "inf"))
        ]
        return self._page(items, merged)

    def _replies(self, channel_id: str, parent_ts: str, merged: dict[str, Any]) -> httpx.Response:
        items = sorted(
            [
                m
                for m in self.messages.get(channel_id, [])
                if m.get("thread_ts") == parent_ts or m["ts"] == parent_ts
            ],
            key=lambda m: m["ts"],
        )
        return self._page(items, merged)

    def load_fixtures(self, directory: Path) -> None:
        """Load the canned universe (users, channels) from fixture JSON files."""
        for user in json.loads((directory / "users.json").read_text()):
            self.add_user(user["id"], user["name"], user["email"], team=user.get("team"))
        for channel in json.loads((directory / "channels.json").read_text()):
            self.add_channel(
                channel["id"],
                channel["name"],
                kind=channel.get("kind", "public"),
                teams=channel.get("teams"),
            )

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def table_count(conninfo: str, table: str, where: str = "", *args: Any) -> int:
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        row = db.execute(f"select count(*) from public.{table} {where}", args).fetchone()  # noqa: S608
        assert row is not None
        return int(row["count"])
