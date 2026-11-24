"""Slack Web API for the owner's internal, read-only app.

Docs checked: 2026-10-05, https://docs.slack.dev/reference/methods/:
auth.test, users.list, users.info, conversations.list/history/replies/members,
chat.getPermalink; https://docs.slack.dev/reference/events/message/ pins edited.user/ts.
https://docs.slack.dev/messaging/retrieving-messages/ pins latest_reply on parent messages.
A 429 suspends the job for Retry-After instead of blocking an executor worker.
"""

from collections.abc import Iterator
from typing import Any

import httpx

from firra.connectors.slack.auth import API, check_response
from firra.http import ProviderError, error_reason, request_with_retry


class SlackRateLimitError(ProviderError):
    def __init__(self, delay_seconds: float) -> None:
        super().__init__(429, "ratelimited")
        self.delay_seconds = delay_seconds


class SlackClient:
    def __init__(self, http: httpx.Client, token: str, *, workspace_url: str | None = None) -> None:
        self.http = http
        self.workspace_url = workspace_url
        self._token = token
        self._links: dict[tuple[str, str], str | None] = {}

    def __repr__(self) -> str:
        return "SlackClient()"

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        try:
            response = request_with_retry(
                self.http,
                "GET",
                f"{API}/{method}",
                attempts=1,
                timeout=15,
                headers={"Authorization": f"Bearer {self._token}"},
                params=params,
            )
        except httpx.TransportError:
            raise ProviderError(503) from None
        if response.status_code == 429:
            value = response.headers.get("retry-after", "60")
            delay = float(value) if value.isdigit() else 60.0
            raise SlackRateLimitError(max(1.0, delay))
        if response.status_code >= 400:
            raise ProviderError(response.status_code, error_reason(response))
        try:
            body = response.json()
        except ValueError:
            raise ProviderError(502, "invalid_response") from None
        if not isinstance(body, dict):
            raise ProviderError(502, "invalid_response")
        # Sanitize unknown provider error text before the shared auth mapper sees it.
        if body.get("ok") is not True:
            reason = error_reason(response)
            if reason == "ratelimited":
                value = response.headers.get("retry-after", "60")
                raise SlackRateLimitError(float(value) if value.isdigit() else 60.0)
            check_response({"ok": False, "error": reason})
            raise ProviderError(502, "invalid_response")
        return body

    def page(
        self, method: str, field: str, **params: Any
    ) -> tuple[list[dict[str, Any]], str | None]:
        body = self.call(method, limit=200, **params)
        values = body.get(field, [])
        if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
            raise ProviderError(502, "invalid_response")
        cursor = str((body.get("response_metadata") or {}).get("next_cursor", "")).strip()
        if body.get("has_more") is True and not cursor:
            raise ProviderError(502, "incomplete_page")
        return values, cursor or None

    def pages(self, method: str, field: str, **params: Any) -> Iterator[dict[str, Any]]:
        cursor = None
        while True:
            values, following = self.page(
                method, field, **params, **({"cursor": cursor} if cursor else {})
            )
            yield from values
            if not following:
                return
            if following == cursor:
                raise ProviderError(502, "invalid_cursor")
            cursor = following

    def auth_test(self) -> dict[str, Any]:
        return self.call("auth.test")

    def users(self) -> Iterator[dict[str, Any]]:
        return self.pages("users.list", "members")

    def user(self, user_id: str) -> dict[str, Any]:
        user = self.call("users.info", user=user_id).get("user")
        if not isinstance(user, dict):
            raise ProviderError(502, "invalid_response")
        return user

    def channels(self) -> Iterator[dict[str, Any]]:
        return self.pages(
            "conversations.list",
            "channels",
            types="public_channel,private_channel,mpim,im",
            exclude_archived="true",
        )

    def members(self, channel: str) -> list[str]:
        # members is a list of ids, unlike collection methods returning objects.
        members: list[str] = []
        cursor = None
        while True:
            body = self.call(
                "conversations.members",
                channel=channel,
                limit=200,
                **({"cursor": cursor} if cursor else {}),
            )
            members.extend(str(value) for value in body.get("members", []))
            following = str((body.get("response_metadata") or {}).get("next_cursor", "")).strip()
            if not following:
                return members
            if following == cursor:
                raise ProviderError(502, "invalid_cursor")
            cursor = following

    def history(self, channel: str, **params: Any) -> tuple[list[dict[str, Any]], str | None]:
        return self.page("conversations.history", "messages", channel=channel, **params)

    def replies(
        self, channel: str, ts: str, **params: Any
    ) -> tuple[list[dict[str, Any]], str | None]:
        return self.page("conversations.replies", "messages", channel=channel, ts=ts, **params)

    def permalink(self, channel: str, ts: str) -> str | None:
        key = (channel, ts)
        if key not in self._links:
            try:
                self._links[key] = str(
                    self.call("chat.getPermalink", channel=channel, message_ts=ts)["permalink"]
                )
            except ProviderError:
                # chat.getPermalink documents this workspace/archives/p<ts> shape.
                self._links[key] = (
                    f"{self.workspace_url.rstrip('/')}/archives/{channel}/p{ts.replace('.', '')}"
                    if self.workspace_url
                    else None
                )
        return self._links[key]
