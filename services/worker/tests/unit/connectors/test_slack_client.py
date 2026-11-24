"""Synthetic Slack Web API responses only; no network or customer records."""

from typing import Any
from uuid import uuid4

import httpx
import pytest

from firra.connectors.errors import TokenRevokedError
from firra.connectors.slack.client import SlackClient, SlackRateLimitError
from firra.connectors.slack.local_tokens import LocalSlackTokens, token_slot
from firra.connectors.slack.normalize import normalize
from firra.http import ProviderError
from firra.local.secrets import SecretStore

TOKEN = "xoxp-synthetic-unit-token"


def test_cursor_pagination_and_read_parameters() -> None:
    calls: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.url.params["types"] == "public_channel,private_channel,mpim,im"
        second = request.url.params.get("cursor") == "next"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "channels": [{"id": "C2" if second else "C1"}],
                "response_metadata": {"next_cursor": "" if second else "next"},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(reply)) as http:
        client = SlackClient(http, TOKEN)
        assert list(client.channels()) == [{"id": "C1"}, {"id": "C2"}]
        assert TOKEN not in repr(client)
    assert len(calls) == 2


@pytest.mark.parametrize("http_status", [200, 429])
def test_retry_after_is_preserved_without_sleep(http_status: int) -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    http_status,
                    headers={"Retry-After": "120"},
                    json={"ok": False, "error": "ratelimited"},
                )
            )
        ) as http,
        pytest.raises(SlackRateLimitError) as exc,
    ):
        SlackClient(http, TOKEN).history("C1")
    assert exc.value.delay_seconds == 120


@pytest.mark.parametrize("code", ["invalid_auth", "token_revoked", "account_inactive"])
def test_revoked_user_tokens_are_safe(code: str) -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"ok": False, "error": code})
            )
        ) as http,
        pytest.raises(TokenRevokedError, match=code),
    ):
        SlackClient(http, TOKEN).auth_test()


def test_error_text_is_not_propagated() -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"ok": False, "error": "private body and token " + TOKEN}
                )
            )
        ) as http,
        pytest.raises(ProviderError) as exc,
    ):
        SlackClient(http, TOKEN).user("U1")
    assert exc.value.reason is None and TOKEN not in str(exc.value)


def test_permalink_cache_and_documented_fallback() -> None:
    calls = []

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    with httpx.Client(transport=httpx.MockTransport(reply)) as http:
        client = SlackClient(http, TOKEN, workspace_url="https://synthetic.slack.com/")
        expected = "https://synthetic.slack.com/archives/C1/p1759658400000001"
        assert client.permalink("C1", "1759658400.000001") == expected
        assert client.permalink("C1", "1759658400.000001") == expected
    assert len(calls) == 1


def test_tokens_are_memory_only_and_deletion_wins() -> None:
    secrets = SecretStore()
    connection_id = uuid4()
    tokens = LocalSlackTokens(secrets)
    secrets.set(token_slot(connection_id), TOKEN)
    assert tokens.access_token(connection_id) == TOKEN and TOKEN not in repr(tokens)
    secrets.delete(token_slot(connection_id))
    with pytest.raises(TokenRevokedError):
        tokens.access_token(connection_id)


def test_normalization_external_author_edit_and_system_drop() -> None:
    message: dict[str, Any] = {
        "type": "message",
        "user": "U2",
        "ts": "1759658400.000001",
        "thread_ts": "1759658000.000001",
        "text": "Please *send* the report <@U1> :smile:",
        "edited": {"user": "U2", "ts": "1759658500.000001"},
    }
    channel = {"id": "C1", "name": "client-room", "is_ext_shared": True}
    user = {"id": "U2", "team_id": "T2", "profile": {"real_name": "Synthetic Client"}}
    record = normalize(message, channel, user, team_id="T1", users={"U1": "Owner"})
    assert record is not None
    assert record.external_id == "C1:1759658400.000001"
    assert record.text == "Please send the report @Owner"
    assert record.edited and record.author.is_external and record.author.email is None
    assert record.thread_key == message["thread_ts"]
    for subtype in ("bot_message", "channel_join", "huddle_start"):
        assert (
            normalize({**message, "subtype": subtype}, channel, user, team_id="T1", users={})
            is None
        )
