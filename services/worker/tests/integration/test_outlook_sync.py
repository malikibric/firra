"""Outlook mail + calendar backfill and incremental sync: fake Graph, local Postgres."""

import dataclasses
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from firra.app import create_app
from firra.connectors.microsoft.auth import MicrosoftAuth
from firra.connectors.microsoft.credentials import MicrosoftAppTokens, MicrosoftUserTokens
from firra.db.types import WorkspaceId
from firra.tasks.context import TaskContext
from tests.conftest import LOCAL_SECRET, local_settings
from tests.fake_microsoft import FakeMicrosoft, graph_event, graph_message
from tests.tokens import local_token

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]
FIRM = "ana@northbeam.agency"
HOUR = timedelta(hours=1)


@pytest.fixture
def db() -> Iterator[Db]:
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as conn:
        yield conn


@dataclasses.dataclass
class World:
    ws: UUID
    db: Db
    app: FastAPI
    client: TestClient
    fake: FakeMicrosoft

    def connection(
        self, provider: str, mode: str = "user", token: dict[str, str] | None = None
    ) -> UUID:
        row = self.db.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label, "
            "external_account_id) values (%s, %s, %s, %s, %s) returning id",
            (self.ws, provider, mode, FIRM, f"oid-{uuid4().hex[:8]}"),
        ).fetchone()
        assert row is not None
        context: TaskContext = self.app.state.task_context
        blob = context.keyring.encrypt_token(
            WorkspaceId(self.ws),
            row["id"],
            json.dumps(token or {"tenant_id": "tenant-1", "refresh_token": "good"}),
        )
        self.db.execute(
            "update public.connection set token_ciphertext = %s where id = %s", (blob, row["id"])
        )
        return UUID(str(row["id"]))

    def run(self, task_type: str, conn: UUID, **payload: object) -> tuple[str, dict[str, Any]]:
        job = self.db.execute(
            "insert into public.job (workspace_id, type, idempotency_key, connection_id) "
            "values (%s, %s, %s, %s) returning id",
            (self.ws, task_type, f"{task_type}-{uuid4()}", conn),
        ).fetchone()
        assert job is not None
        response = self.client.post(
            f"/tasks/{task_type}",
            headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
            json={
                "workspace_id": str(self.ws),
                "job_id": str(job["id"]),
                "idempotency_key": "k",
                "type": task_type,
                "payload": {"connection_id": str(conn), **payload},
            },
        )
        row = self.db.execute(
            "select progress, error_class from public.job where id = %s", (job["id"],)
        ).fetchone()
        assert row is not None
        return str(response.json()["status"]), {**row["progress"], "error": row["error_class"]}

    def cursor(self, conn: UUID) -> dict[str, Any]:
        row = self.db.execute(
            "select cursor from public.connection where id = %s", (conn,)
        ).fetchone()
        assert row is not None
        return dict(row["cursor"])


@pytest.fixture
def world(db: Db) -> Iterator[World]:
    ws = uuid4()
    db.execute("insert into public.workspace (id, name) values (%s, 'Northbeam')", (ws,))
    client_row = db.execute(
        "insert into public.client (workspace_id, name) values (%s, 'Acme') returning id", (ws,)
    ).fetchone()
    assert client_row is not None
    db.execute(
        "insert into public.client_domain (workspace_id, client_id, domain) "
        "values (%s, %s, 'acme.com')",
        (ws, client_row["id"]),
    )
    fake = FakeMicrosoft()
    app = create_app(local_settings())
    context: TaskContext = app.state.task_context
    http = fake.client()
    app.state.task_context = dataclasses.replace(
        context,
        http=http,
        microsoft=MicrosoftAuth(
            user=MicrosoftUserTokens("cid", "secret", http),
            app=MicrosoftAppTokens("cid", "secret", http),
            keyring=context.keyring,
            connections=context.connections,
        ),
    )
    with TestClient(app) as client:
        yield World(ws=ws, db=db, app=app, client=client, fake=fake)
    db.execute("delete from public.workspace where id = %s", (ws,))


def test_mail_backfill_pages_through_the_window(world: World) -> None:
    conn = world.connection("outlook")
    fake = world.fake
    for i in range(1, 4):
        fake.messages[f"acme-{i}"] = graph_message(
            f"acme-{i}", f"2026-09-0{i}T10:00:00Z", sender="ceo@acme.com", to=[FIRM]
        )
    fake.messages["vendor"] = graph_message(
        "vendor", "2026-09-02T11:00:00Z", sender="x@vendor.com", to=[FIRM]
    )
    fake.messages["junk"] = graph_message(
        "junk", "2026-09-02T12:00:00Z", sender="ceo@acme.com", to=[FIRM], folder="junkemail"
    )
    fake.messages["draft"] = graph_message(
        "draft", "2026-09-02T13:00:00Z", sender=FIRM, to=["ceo@acme.com"], is_draft=True
    )
    fake.messages["august"] = graph_message(
        "august", "2026-08-31T10:00:00Z", sender="ceo@acme.com", to=[FIRM]
    )

    status, progress = world.run(
        "sync.backfill_chunk",
        conn,
        window_start="2026-09-01T00:00:00Z",
        window_end="2026-10-01T00:00:00Z",
    )
    assert status == "succeeded"
    assert progress["kept"] == 3 and progress["invalid"] == 2
    assert progress["dropped"] == {"no_client": 1} and progress["page_token"] is None
    records = world.db.execute(
        "select external_id, provider::text, thread_key, deep_link, body_clean "
        "from public.source_record where workspace_id = %s order by external_id",
        (world.ws,),
    ).fetchall()
    assert [r["external_id"] for r in records] == ["acme-1", "acme-2", "acme-3"]
    assert records[0]["provider"] == "outlook" and records[0]["thread_key"] == "conv-1"
    assert records[0]["body_clean"] == "Hello, here is the update."
    # Immutable ids and text bodies were requested on every Graph call.
    assert all('IdType="ImmutableId"' in h for h in fake.prefer_headers)
    assert any('outlook.body-content-type="text"' in h for h in fake.prefer_headers)


def test_mail_incremental_follows_the_received_watermark(world: World) -> None:
    conn = world.connection("outlook")
    world.fake.rotate_refresh_token_to = "rotated"
    status, first = world.run("sync.incremental", conn)
    assert status == "succeeded" and first["initialized"] is True
    assert datetime.fromisoformat(world.cursor(conn)["received_after"]) <= datetime.now(UTC)
    # Pretend the last sync was an hour ago.
    watermark = datetime.now(UTC).replace(microsecond=0) - HOUR
    world.db.execute(
        "update public.connection set cursor = %s where id = %s",
        (json.dumps({"received_after": watermark.isoformat()}), conn),
    )

    received = (watermark + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    world.fake.messages["new-1"] = graph_message(
        "new-1", received, sender="ceo@acme.com", to=[FIRM]
    )
    status, second = world.run("sync.incremental", conn)
    assert status == "succeeded" and second["kept"] == 1
    assert world.cursor(conn)["received_after"] == received.replace("Z", "+00:00")

    # The overlap re-reads the last message: an update, not a duplicate.
    status, third = world.run("sync.incremental", conn)
    assert status == "succeeded" and third["kept"] == 0 and third["updated"] == 1

    # The rotated refresh token replaced the stored one.
    row = world.db.execute(
        "select token_ciphertext from public.connection where id = %s", (conn,)
    ).fetchone()
    assert row is not None
    context: TaskContext = world.app.state.task_context
    stored = json.loads(
        context.keyring.decrypt_token(WorkspaceId(world.ws), conn, row["token_ciphertext"])
    )
    assert stored == {"tenant_id": "tenant-1", "refresh_token": "rotated"}


def test_revoked_consent_marks_the_connection_revoked(world: World) -> None:
    conn = world.connection("outlook", token={"tenant_id": "tenant-1", "refresh_token": "bad"})
    world.fake.revoked_refresh_tokens.add("bad")
    status, progress = world.run("sync.incremental", conn)
    assert status == "failed" and progress["error"] == "token_revoked"
    row = world.db.execute(
        "select status::text from public.connection where id = %s", (conn,)
    ).fetchone()
    assert row == {"status": "revoked"}


def test_application_permission_mode_reads_the_selected_mailbox(world: World) -> None:
    conn = world.connection("outlook", mode="org_app", token={"tenant_id": "tenant-1"})
    status, _ = world.run(
        "sync.backfill_chunk",
        conn,
        window_start="2026-09-01T00:00:00Z",
        window_end="2026-10-01T00:00:00Z",
    )
    assert status == "succeeded"
    graph_calls = [c for c in world.fake.calls if c.startswith("GET /v1.0/")]
    assert graph_calls and all(c.startswith("GET /v1.0/users/oid-") for c in graph_calls)


def statuses(world: World) -> dict[str, str]:
    rows = world.db.execute(
        "select r.external_id, e.status::text as status from public.calendar_event e "
        "join public.source_record r on r.id = e.source_record_id where r.workspace_id = %s",
        (world.ws,),
    ).fetchall()
    return {r["external_id"]: r["status"] for r in rows}


def test_calendar_backfill_and_delta_sync(world: World) -> None:
    conn = world.connection("outlook_calendar")
    fake = world.fake
    now = datetime.now(UTC).replace(microsecond=0)
    past, future = now - timedelta(days=3), now + timedelta(days=3)
    fake.put_event(graph_event("held", past, past + HOUR, ["ceo@acme.com"]))
    fake.put_event(graph_event("cancelled", past, past + HOUR, ["ceo@acme.com"], cancelled=True))
    fake.put_event(
        graph_event(
            "moved", past, past + HOUR, ["ceo@acme.com"], original_start=past - timedelta(days=1)
        )
    )
    fake.put_event(graph_event("internal", past, past + HOUR, ["ben@northbeam.agency"]))
    fake.put_event(graph_event("offsite", past, past + HOUR, ["ceo@acme.com"], all_day=True))

    status, backfill = world.run(
        "sync.backfill_chunk",
        conn,
        window_start=(now - timedelta(days=30)).isoformat(),
        window_end=now.isoformat(),
    )
    assert status == "succeeded"
    assert backfill["kept"] == 3 and backfill["invalid"] == 1
    assert backfill["dropped"] == {"no_client": 1}
    assert statuses(world) == {"held": "held", "cancelled": "cancelled", "moved": "rescheduled"}

    # First delta round: upcoming events inside [now - 1 day, now + 365 days].
    fake.put_event(graph_event("upcoming", future, future + HOUR, ["ceo@acme.com"]))
    fake.put_event(graph_event("dropped", future, future + HOUR, ["ceo@acme.com"]))
    status, first = world.run("sync.incremental", conn)
    assert status == "succeeded" and first["kept"] == 2 and first["sync_reset"] is False
    cursor = world.cursor(conn)
    assert cursor["delta_link"].endswith(f"$deltatoken={len(fake.event_log)}")

    # The client cancels one meeting; the organizer deletes the other.
    fake.put_event(graph_event("upcoming", future, future + HOUR, ["ceo@acme.com"], cancelled=True))
    fake.delete_event("dropped")
    status, second = world.run("sync.incremental", conn)
    assert status == "succeeded" and second["cancelled"] == 1
    assert statuses(world)["upcoming"] == "cancelled"
    assert statuses(world)["dropped"] == "cancelled"
    assert statuses(world)["held"] == "held"

    # An expired delta state starts a new round.
    fake.expired_delta_tokens.add(str(len(fake.event_log)))
    status, reset = world.run("sync.incremental", conn)
    assert status == "succeeded" and reset["sync_reset"] is True


def test_incremental_sync_keeps_a_change_notification_subscription(world: World) -> None:
    context: TaskContext = world.app.state.task_context
    world.app.state.task_context = dataclasses.replace(
        context, microsoft_notification_url="https://api.example/webhooks/microsoft"
    )
    conn = world.connection("outlook")
    status, first = world.run("sync.incremental", conn)
    assert status == "succeeded" and first["push_renewed"] is True
    cursor = world.cursor(conn)
    # The push state is merged next to the sync cursor, not replacing it.
    assert "received_after" in cursor and cursor["push"]["subscription_id"]
    [sub] = world.fake.subscriptions.values()
    assert f"c={conn}" in sub["notificationUrl"] and f"w={world.ws}" in sub["notificationUrl"]

    status, second = world.run("sync.incremental", conn)
    assert status == "succeeded" and "push_renewed" not in second
    status, forced = world.run("sync.incremental", conn, renew_push=True)
    assert status == "succeeded" and forced["push_renewed"] is True
    assert world.cursor(conn)["push"]["subscription_id"] == cursor["push"]["subscription_id"]

    calendar = world.connection("outlook_calendar")
    status, _ = world.run("sync.incremental", calendar)
    assert status == "succeeded"
    assert sorted(s["resource"] for s in world.fake.subscriptions.values()) == [
        "me/events",
        "me/messages",
    ]
