from datetime import UTC, datetime, timedelta

import httpx
import pytest

from firra.connectors.errors import TokenRevokedError
from firra.connectors.microsoft.calendar import to_record
from firra.connectors.microsoft.credentials import (
    MicrosoftAppTokens,
    MicrosoftUserTokens,
    StoredMicrosoftTokens,
)
from firra.connectors.microsoft.graph import GraphClient, graph_datetime
from firra.connectors.microsoft.outlook import received_filter, to_message
from firra.http import ProviderError
from tests.fake_microsoft import FakeMicrosoft, graph_event, graph_message

FIRM = "ana@northbeam.agency"
EXCLUDED = frozenset({"folder-junkemail", "folder-deleteditems", "folder-drafts"})
START = datetime(2026, 9, 1, 10, tzinfo=UTC)


def test_graph_datetime_accepts_seven_fraction_digits_and_missing_zone() -> None:
    assert graph_datetime("2026-09-01T10:00:00.1234567") == datetime(
        2026, 9, 1, 10, 0, 0, 123456, tzinfo=UTC
    )
    assert graph_datetime("2026-09-01T10:00:00Z") == START


def test_message_mapping() -> None:
    raw = graph_message("m1", "2026-09-01T10:00:00Z", sender="CEO@Acme.com", to=[FIRM])
    message = to_message(raw, EXCLUDED)
    assert message is not None
    assert message.provider.value == "outlook" and message.external_id == "m1"
    assert message.sender.email == "ceo@acme.com" and message.to[0].email == FIRM
    assert message.thread_key == "conv-1" and message.occurred_at == START
    assert message.deep_link == "https://outlook.office365.com/owa/?ItemID=m1"
    assert received_filter(START, None) == "receivedDateTime ge 2026-09-01T10:00:00Z"


def test_html_bodies_are_converted_to_text() -> None:
    raw = graph_message(
        "m1",
        "2026-09-01T10:00:00Z",
        sender="ceo@acme.com",
        to=[FIRM],
        body="<html><style>p{}</style><p>Renewal is <b>on track</b></p></html>",
        content_type="html",
    )
    message = to_message(raw, EXCLUDED)
    assert message is not None and message.body_text is not None
    assert "Renewal is on track" in message.body_text and "<" not in message.body_text


@pytest.mark.parametrize(
    "overrides",
    [
        {"isDraft": True},
        {"parentFolderId": "folder-junkemail"},
        {"from": {"emailAddress": {"address": "/O=EXCHANGELABS/OU=EXCHANGE/CN=ANA"}}},
        {"sentDateTime": None, "receivedDateTime": None},
    ],
)
def test_messages_that_are_never_ingested(overrides: dict[str, object]) -> None:
    raw = {
        **graph_message("m1", "2026-09-01T10:00:00Z", sender="ceo@acme.com", to=[FIRM]),
        **overrides,
    }
    assert to_message(raw, EXCLUDED) is None


def test_event_mapping() -> None:
    hour = timedelta(hours=1)
    record = to_record(graph_event("e1", START, START + hour, ["ceo@acme.com"]))
    assert record is not None
    assert record.provider.value == "outlook_calendar"
    assert (record.starts_at, record.ends_at) == (START, START + hour)
    assert record.status.value == "confirmed" and record.rescheduled is False
    assert record.organizer.email == FIRM and [a.email for a in record.attendees] == [
        "ceo@acme.com"
    ]

    moved = to_record(
        graph_event("e2", START, START + hour, [], original_start=START - timedelta(days=1))
    )
    cancelled = to_record(graph_event("e3", START, START + hour, [], cancelled=True))
    assert moved is not None and moved.rescheduled is True
    assert cancelled is not None and cancelled.status.value == "cancelled"
    assert to_record(graph_event("e4", START, START + hour, [], all_day=True)) is None
    local = graph_event("e5", START, START + hour, [])
    local["start"]["timeZone"] = "Pacific Standard Time"
    assert to_record(local) is None


def test_user_tokens_rotate_and_detect_revocation() -> None:
    fake = FakeMicrosoft(revoked_refresh_tokens={"revoked"}, rotate_refresh_token_to="new")
    tokens = MicrosoftUserTokens("cid", "secret", fake.client())
    grant = tokens.access_token(StoredMicrosoftTokens("tenant-1", "old"), "Mail.Read")
    assert grant.access_token == "access-ok" and grant.rotated_refresh_token == "new"
    assert fake.calls[0] == "POST /tenant-1/oauth2/v2.0/token"
    with pytest.raises(TokenRevokedError):
        tokens.access_token(StoredMicrosoftTokens("tenant-1", "revoked"), "Mail.Read")
    with pytest.raises(TokenRevokedError):
        tokens.access_token(StoredMicrosoftTokens("tenant-1"), "Mail.Read")


def test_app_tokens_use_the_customer_tenant() -> None:
    fake = FakeMicrosoft(revoked_tenants={"gone"})
    tokens = MicrosoftAppTokens("cid", "secret", fake.client())
    assert tokens.access_token("tenant-1") == "app-ok"
    with pytest.raises(TokenRevokedError):
        tokens.access_token("gone")
    with pytest.raises(ValueError, match="tenant"):
        tokens.access_token("../evil")


def test_stored_tokens_round_trip() -> None:
    stored = StoredMicrosoftTokens("tenant-1", "refresh")
    assert StoredMicrosoftTokens.from_json(stored.to_json()) == stored
    assert StoredMicrosoftTokens.from_json('{"tenant_id": "t"}').refresh_token is None


def test_graph_client_follows_links_only_to_graph() -> None:
    graph = GraphClient(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))), "t", "/me"
    )
    for link in ("https://evil.example/v1.0/me/messages", "http://graph.microsoft.com/v1.0/me"):
        with pytest.raises(ProviderError):
            graph.page(link)
