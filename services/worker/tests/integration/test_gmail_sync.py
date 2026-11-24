"""Gmail backfill (with continuations) and incremental sync: fake Gmail, local Postgres."""

import dataclasses
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from firra.app import create_app
from firra.connectors.google.auth import GoogleAuth
from firra.connectors.google.credentials import GoogleUserTokens
from firra.db.types import WorkspaceId
from firra.tasks.context import TaskContext
from tests.conftest import LOCAL_SECRET, local_settings
from tests.fake_google import FakeGoogle, stored_tokens
from tests.mail import email_bytes
from tests.tokens import local_token

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]
FIRM = "ana@northbeam.agency"


class CapturingQueue:
    def __init__(self) -> None:
        self.envelopes: list[Any] = []
        self.delays: list[float] = []

    def enqueue(self, envelope: Any, *, delay_seconds: float = 0) -> None:
        self.envelopes.append(envelope)
        self.delays.append(delay_seconds)


@pytest.fixture
def db() -> Iterator[Db]:
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as conn:
        yield conn


@dataclasses.dataclass
class World:
    ws: UUID
    conn: UUID
    app: FastAPI
    client: TestClient
    fake: FakeGoogle
    queue: CapturingQueue


@pytest.fixture
def world(db: Db) -> Iterator[World]:
    ws = uuid4()
    db.execute("insert into public.workspace (id, name) values (%s, 'Northbeam')", (ws,))
    client = db.execute(
        "insert into public.client (workspace_id, name) values (%s, 'Acme') returning id", (ws,)
    ).fetchone()
    assert client is not None
    db.execute(
        "insert into public.client_domain (workspace_id, client_id, domain) "
        "values (%s, %s, 'acme.com')",
        (ws, client["id"]),
    )
    row = db.execute(
        "insert into public.connection (workspace_id, provider, mode, account_label, "
        "external_account_id) values (%s, 'gmail', 'user', %s, %s) returning id",
        (ws, FIRM, f"g-{ws.hex[:8]}"),
    ).fetchone()
    assert row is not None

    fake, queue = FakeGoogle(revoked_refresh_tokens={"revoked"}), CapturingQueue()
    app = create_app(local_settings(task_time_budget_seconds=0.001))
    context: TaskContext = app.state.task_context
    http = fake.client()
    app.state.task_context = dataclasses.replace(
        context,
        http=http,
        queue=queue,
        google=GoogleAuth(
            user=GoogleUserTokens("cid", "secret", http), delegated=None, keyring=context.keyring
        ),
    )
    # One client for the whole test: the connection pool closes when its lifespan ends.
    with TestClient(app) as api:
        yield World(ws=ws, conn=row["id"], app=app, client=api, fake=fake, queue=queue)
    db.execute("delete from public.workspace where id = %s", (ws,))


def store_token(world: World, db: Db, refresh_token: str) -> None:
    context: TaskContext = world.app.state.task_context
    blob = context.keyring.encrypt_token(
        WorkspaceId(world.ws), world.conn, stored_tokens(refresh_token)
    )
    db.execute(
        "update public.connection set token_ciphertext = %s where id = %s", (blob, world.conn)
    )


def new_job(db: Db, world: World, task_type: str) -> UUID:
    row = db.execute(
        "insert into public.job (workspace_id, type, idempotency_key, connection_id) "
        "values (%s, %s, %s, %s) returning id",
        (world.ws, task_type, f"{task_type}-{uuid4()}", world.conn),
    ).fetchone()
    assert row is not None
    return UUID(str(row["id"]))


def post(
    world: World, task_type: str, job_id: UUID, payload: dict[str, Any], key: str = "k"
) -> str:
    response = world.client.post(
        f"/tasks/{task_type}",
        headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
        json={
            "workspace_id": str(world.ws),
            "job_id": str(job_id),
            "idempotency_key": key,
            "type": task_type,
            "payload": payload,
        },
    )
    return str(response.json()["status"])


def september(day: int) -> str:
    weekday = ["Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Mon"][(day - 1) % 7]
    return f"{weekday}, {day:02d} Sep 2026 10:00:00 +0000"


def test_backfill_continues_until_the_window_is_done(db: Db, world: World) -> None:
    store_token(world, db, "good")
    for i in range(1, 6):
        world.fake.add(
            f"acme-{i}",
            email_bytes(
                sender="ceo@acme.com",
                to=[FIRM],
                date=september(i),
                message_id=f"<acme-{i}@acme.com>",
            ),
            thread_id=f"t{i}",
        )
    world.fake.add(
        "vendor",
        email_bytes(
            sender="x@vendor.com", to=[FIRM], date=september(2), message_id="<v@vendor.com>"
        ),
    )
    world.fake.add(
        "spam",
        email_bytes(
            sender="ceo@acme.com", to=[FIRM], date=september(3), message_id="<spam@acme.com>"
        ),
        labels=["SPAM"],
    )
    world.fake.add(
        "august",
        email_bytes(
            sender="ceo@acme.com",
            to=[FIRM],
            date="Mon, 31 Aug 2026 10:00:00 +0000",
            message_id="<aug@acme.com>",
        ),
    )
    payload = {
        "connection_id": str(world.conn),
        "window_start": "2026-09-01T00:00:00Z",
        "window_end": "2026-10-01T00:00:00Z",
    }
    job = new_job(db, world, "sync.backfill_chunk")

    rounds = 0
    status = post(world, "sync.backfill_chunk", job, payload)
    while status == "continued":
        rounds += 1
        follow_up = world.queue.envelopes.pop()
        status = post(world, "sync.backfill_chunk", job, payload, key=follow_up.idempotency_key)
    assert status == "succeeded" and rounds >= 2, "several pages -> several continuations"

    row = db.execute(
        "select status, attempts, progress from public.job where id = %s", (job,)
    ).fetchone()
    assert row is not None and row["status"] == "succeeded"
    progress = row["progress"]
    assert (
        progress["kept"] == 5
        and progress["invalid"] == 1
        and progress["dropped"] == {"no_client": 1}
    )
    assert progress["page_token"] is None and "completed_at" in progress

    records = db.execute(
        "select external_id, thread_key, deep_link from public.source_record "
        "where workspace_id = %s order by external_id",
        (world.ws,),
    ).fetchall()
    assert [r["external_id"] for r in records] == [f"acme-{i}" for i in range(1, 6)]
    assert records[0]["thread_key"] == "t1"
    assert records[0]["deep_link"] == "https://mail.google.com/mail/u/0/#all/acme-1"
    # Headers for all 7 messages in the window; full content only for the 5 client messages.
    assert world.fake.formats.count("metadata") == 7
    assert world.fake.formats.count("raw") == 5


def test_backfill_rides_out_gmail_throttling(
    db: Db, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("firra.http.time.sleep", sleeps.append)
    store_token(world, db, "good")
    world.fake.add(
        "acme-1",
        email_bytes(sender="ceo@acme.com", to=[FIRM], date=september(1), message_id="<a@acme.com>"),
    )
    world.fake.throttle = 3
    payload = {
        "connection_id": str(world.conn),
        "window_start": "2026-09-01T00:00:00Z",
        "window_end": "2026-10-01T00:00:00Z",
    }
    job = new_job(db, world, "sync.backfill_chunk")

    assert post(world, "sync.backfill_chunk", job, payload) == "succeeded"
    row = db.execute("select attempts, progress from public.job where id = %s", (job,)).fetchone()
    assert row is not None and row["attempts"] == 1 and row["progress"]["kept"] == 1
    assert sleeps == [1.0, 2.0, 4.0]


def test_a_mailbox_that_stays_throttled_waits_instead_of_failing(
    db: Db, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("firra.http.time.sleep", lambda _s: None)
    store_token(world, db, "good")
    world.fake.throttle = 100
    payload = {
        "connection_id": str(world.conn),
        "window_start": "2026-09-01T00:00:00Z",
        "window_end": "2026-10-01T00:00:00Z",
    }
    job = new_job(db, world, "sync.backfill_chunk")
    db.execute(
        'update public.job set progress = \'{"page_token": "2", "kept": 2}\' where id = %s',
        (job,),
    )

    assert post(world, "sync.backfill_chunk", job, payload) == "continued"
    row = db.execute(
        "select status, attempts, progress from public.job where id = %s", (job,)
    ).fetchone()
    assert row is not None and row["status"] == "queued" and row["attempts"] == 0
    assert row["progress"]["page_token"] == "2" and row["progress"]["kept"] == 2
    assert world.queue.delays == [300]


def running_job(db: Db, world: World, started_ago: str, idle_for: str = "0 seconds") -> None:
    job = new_job(db, world, "sync.backfill_chunk")
    # updated_at is set by a trigger on every update; switch it off to age a stale job.
    db.execute("alter table public.job disable trigger job_set_updated_at")
    try:
        db.execute(
            "update public.job set status = 'running', started_at = now() - %s::interval, "
            "updated_at = now() - %s::interval where id = %s",
            (started_ago, idle_for, job),
        )
    finally:
        db.execute("alter table public.job enable trigger job_set_updated_at")


def test_backfill_waits_while_two_months_of_the_mailbox_run(db: Db, world: World) -> None:
    store_token(world, db, "good")
    payload = {
        "connection_id": str(world.conn),
        "window_start": "2026-09-01T00:00:00Z",
        "window_end": "2026-10-01T00:00:00Z",
    }
    running_job(db, world, "5 minutes")
    running_job(db, world, "4 minutes")
    running_job(db, world, "30 minutes", idle_for="20 minutes")  # stale: instance lost
    job = new_job(db, world, "sync.backfill_chunk")

    assert post(world, "sync.backfill_chunk", job, payload) == "continued"
    row = db.execute("select status, attempts from public.job where id = %s", (job,)).fetchone()
    assert row == {"status": "queued", "attempts": 0}
    assert world.queue.delays == [60]
    assert not [c for c in world.fake.calls if "/gmail/" in c], "no Gmail call while waiting"

    db.execute(
        "update public.job set status = 'succeeded' where workspace_id = %s and status = 'running' "
        "and id <> %s and started_at < now() - interval '4 minutes 30 seconds'",
        (world.ws, job),
    )
    assert post(world, "sync.backfill_chunk", job, payload, key="k2") == "succeeded"


def test_revoked_token_marks_the_connection_revoked(db: Db, world: World) -> None:
    store_token(world, db, "revoked")
    job = new_job(db, world, "sync.incremental")
    assert post(world, "sync.incremental", job, {"connection_id": str(world.conn)}) == "failed"
    job_row = db.execute("select error_class from public.job where id = %s", (job,)).fetchone()
    conn_row = db.execute(
        "select status, last_error_class from public.connection where id = %s", (world.conn,)
    ).fetchone()
    assert job_row is not None and job_row["error_class"] == "token_revoked"
    assert conn_row == {"status": "revoked", "last_error_class": "token_revoked"}


def test_incremental_sync_follows_history(db: Db, world: World) -> None:
    store_token(world, db, "good")
    first = new_job(db, world, "sync.incremental")
    assert post(world, "sync.incremental", first, {"connection_id": str(world.conn)}) == "succeeded"
    cursor = db.execute(
        "select cursor from public.connection where id = %s", (world.conn,)
    ).fetchone()
    assert cursor is not None and cursor["cursor"] == {"history_id": "100"}

    world.fake.add(
        "new-1",
        email_bytes(sender="ceo@acme.com", to=[FIRM], message_id="<n1@acme.com>"),
        record_history=True,
    )
    second = new_job(db, world, "sync.incremental")
    assert (
        post(world, "sync.incremental", second, {"connection_id": str(world.conn)}) == "succeeded"
    )
    world.fake.delete("new-1")
    third = new_job(db, world, "sync.incremental")
    assert post(world, "sync.incremental", third, {"connection_id": str(world.conn)}) == "succeeded"

    progress = db.execute("select progress from public.job where id = %s", (second,)).fetchone()
    assert progress is not None and progress["progress"]["kept"] == 1
    deleted = db.execute("select progress from public.job where id = %s", (third,)).fetchone()
    assert deleted is not None and deleted["progress"]["deleted"] == 1
    remaining = db.execute(
        "select count(*) as n from public.source_record where workspace_id = %s", (world.ws,)
    ).fetchone()
    assert remaining is not None and remaining["n"] == 0
    synced = db.execute(
        "select cursor, last_synced_at from public.connection where id = %s", (world.conn,)
    ).fetchone()
    assert synced is not None and synced["cursor"] == {"history_id": "102"}
    assert synced["last_synced_at"] <= datetime.now(UTC)


def test_incremental_sync_renews_the_gmail_watch_daily(db: Db, world: World) -> None:
    store_token(world, db, "good")
    context: TaskContext = world.app.state.task_context
    world.app.state.task_context = dataclasses.replace(
        context, gmail_push_topic="projects/firra-staging/topics/gmail-push"
    )
    incremental = {"connection_id": str(world.conn)}
    for _ in range(2):
        job = new_job(db, world, "sync.incremental")
        assert post(world, "sync.incremental", job, incremental) == "succeeded"
    assert world.fake.watch_topics == ["projects/firra-staging/topics/gmail-push"]
    cursor = db.execute(
        "select cursor from public.connection where id = %s", (world.conn,)
    ).fetchone()
    assert cursor is not None and cursor["cursor"]["history_id"] == "100"
    assert cursor["cursor"]["push"]["expires_at"].startswith("2026-09-21")

    job = new_job(db, world, "sync.incremental")
    forced = {**incremental, "renew_push": True}
    assert post(world, "sync.incremental", job, forced) == "succeeded"
    assert len(world.fake.watch_topics) == 2
