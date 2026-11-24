import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from firra.connectors.microsoft.graph import GraphClient
from firra.connectors.microsoft.subscriptions import PushTarget, ensure_subscription
from tests.fake_microsoft import FakeMicrosoft

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
WS, CONN = uuid4(), uuid4()
TARGET = PushTarget("https://api.example/webhooks/microsoft", WS, CONN, "messages")


def graph(fake: FakeMicrosoft, user_path: str = "/me") -> GraphClient:
    return GraphClient(fake.client(), "access-ok", user_path)


def test_creates_a_routable_subscription_and_keeps_only_a_hash_of_the_client_state() -> None:
    fake = FakeMicrosoft()
    push = ensure_subscription(graph(fake), TARGET, None, now=NOW)
    assert push is not None
    [sub] = fake.subscriptions.values()
    assert sub["resource"] == "me/messages" and sub["changeType"] == "created,updated,deleted"
    assert sub["expirationDateTime"] == "2026-10-01T12:00:00Z"
    url = urlsplit(sub["notificationUrl"])
    assert url.netloc == "api.example" and url.path == "/webhooks/microsoft"
    assert parse_qs(url.query) == {"w": [str(WS)], "c": [str(CONN)]}
    assert sub["lifecycleNotificationUrl"] == sub["notificationUrl"]
    assert 32 <= len(sub["clientState"]) <= 128
    assert push["client_state_sha256"] == hashlib.sha256(sub["clientState"].encode()).hexdigest()
    assert sub["clientState"] not in str(push)
    assert push["subscription_id"] == sub["id"] and push["renewed_at"] == NOW.isoformat()


def test_renews_only_when_due_or_forced() -> None:
    fake = FakeMicrosoft()
    push = ensure_subscription(graph(fake), TARGET, None, now=NOW)
    assert ensure_subscription(graph(fake), TARGET, push, now=NOW + timedelta(hours=1)) is None
    later = NOW + timedelta(hours=7)
    renewed = ensure_subscription(graph(fake), TARGET, push, now=later)
    assert renewed is not None and push is not None
    assert renewed["subscription_id"] == push["subscription_id"]
    assert renewed["client_state_sha256"] == push["client_state_sha256"]
    assert (
        fake.subscriptions[push["subscription_id"]]["expirationDateTime"] == "2026-10-01T19:00:00Z"
    )
    forced = ensure_subscription(graph(fake), TARGET, renewed, now=later, force=True)
    assert forced is not None and len(fake.subscriptions) == 1


def test_recreates_expired_or_untracked_subscriptions() -> None:
    fake = FakeMicrosoft()
    gone = {
        "subscription_id": "missing",
        "client_state_sha256": "x",
        "renewed_at": "2026-01-01T00:00:00+00:00",
    }
    push = ensure_subscription(graph(fake), TARGET, gone, now=NOW)
    assert push is not None and push["subscription_id"] != "missing"
    # A subscription exists that this connection lost track of (409 on create): replace it.
    replaced = ensure_subscription(graph(fake), TARGET, None, now=NOW)
    assert replaced is not None and list(fake.subscriptions) == [replaced["subscription_id"]]


def test_application_permission_connections_subscribe_to_the_user_resource() -> None:
    fake = FakeMicrosoft()
    events = PushTarget(TARGET.notification_url, WS, CONN, "events")
    ensure_subscription(graph(fake, "/users/oid-1"), events, None, now=NOW)
    assert [s["resource"] for s in fake.subscriptions.values()] == ["users/oid-1/events"]
