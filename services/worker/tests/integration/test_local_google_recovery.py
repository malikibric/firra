"""Local history recovery resumes persisted pages; existing cloud fixtures remain unchanged."""

import dataclasses
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
from psycopg.types.json import Jsonb

from firra.connectors.google.local_tokens import LocalGoogleTokens
from firra.db.connections import ConnectionRepo
from firra.local.secrets import SecretStore
from tests.integration.test_gmail_sync import (
    FIRM,
    Db,
    World,
    new_job,
    post,
)
from tests.integration.test_gmail_sync import (
    db as gmail_db,
)
from tests.integration.test_gmail_sync import (
    world as gmail_world,
)
from tests.mail import email_bytes

db = gmail_db
world = gmail_world

pytestmark = pytest.mark.integration


def configure(world: World) -> None:
    ctx = world.app.state.task_context
    secrets = SecretStore()
    secrets.set(f"google_refresh_token:{world.conn}", "good")
    auth = LocalGoogleTokens(
        ctx.http,
        secrets,
        client_id="desktop",
        client_secret=None,
        send_secret=lambda *_: None,
        on_revoked=lambda _: None,
    )
    world.app.state.task_context = dataclasses.replace(
        ctx,
        google=auth,
        connections=ConnectionRepo(ctx.pool, local=True),
        gmail_push_topic="projects/firra-staging/topics/gmail-push",
    )


def test_expired_history_resumes_its_window_after_restart(db: Db, world: World) -> None:
    configure(world)
    for index in range(5):
        world.fake.add(
            str(index),
            email_bytes(
                sender="ceo@acme.com",
                to=[FIRM],
                date=format_datetime(datetime.now(UTC) - timedelta(days=index + 1)),
                message_id=f"<local-{index}@acme.com>",
            ),
        )
    db.execute(
        "update public.connection set cursor=%s where workspace_id=%s and id=%s",
        (Jsonb({"history_id": "1"}), world.ws, world.conn),
    )
    job = new_job(db, world, "sync.incremental")
    payload = {"connection_id": str(world.conn), "renew_push": True}
    assert post(world, "sync.incremental", job, payload) == "continued"
    progress = db.execute(
        "select progress from public.job where workspace_id=%s and id=%s", (world.ws, job)
    ).fetchone()
    assert progress is not None and progress["progress"]["page_token"] == "2"
    state = db.execute(
        "select cursor from public.connection where workspace_id=%s and id=%s",
        (world.ws, world.conn),
    ).fetchone()
    assert state is not None and state["cursor"]["gmail_resync"]["history_id"] == "100"
    # Replace the in-memory resolver, like a restarted engine with the app's Keychain grant.
    configure(world)
    status = "continued"
    while status == "continued":
        envelope = world.queue.envelopes.pop()
        status = post(world, "sync.incremental", job, payload, key=envelope.idempotency_key)
    assert status == "succeeded"
    assert world.fake.formats.count("metadata") == 5
    assert world.fake.formats.count("raw") == 5
    assert not world.fake.watch_topics
    row = db.execute(
        "select count(*) as n from public.source_record where workspace_id=%s", (world.ws,)
    ).fetchone()
    assert row is not None and row["n"] == 5
    next_job = new_job(db, world, "sync.incremental")
    assert post(world, "sync.incremental", next_job, payload) == "succeeded"
    assert world.fake.formats.count("metadata") == 5


def test_local_twelve_month_backfill_resumes_after_restart(db: Db, world: World) -> None:
    configure(world)
    now = datetime.now(UTC)
    for index in range(5):
        world.fake.add(
            str(index),
            email_bytes(
                sender="ceo@acme.com",
                to=[FIRM],
                date=format_datetime(now - timedelta(days=300)),
                message_id=f"<old-{index}@acme.com>",
            ),
        )
    job = new_job(db, world, "sync.backfill_chunk")
    payload = {
        "connection_id": str(world.conn),
        "window_start": (now - timedelta(days=365)).isoformat(),
        "window_end": now.isoformat(),
    }
    assert post(world, "sync.backfill_chunk", job, payload) == "continued"
    configure(world)
    status = "continued"
    while status == "continued":
        envelope = world.queue.envelopes.pop()
        status = post(world, "sync.backfill_chunk", job, payload, key=envelope.idempotency_key)
    assert status == "succeeded"
    assert world.fake.formats.count("metadata") == 5
    assert world.fake.formats.count("raw") == 5


def test_local_throttle_keeps_checkpoint_and_waits(
    db: Db, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(world)
    monkeypatch.setattr("firra.http.time.sleep", lambda _: None)
    world.fake.throttle = 100
    now = datetime.now(UTC)
    job = new_job(db, world, "sync.backfill_chunk")
    db.execute(
        "update public.job set progress=%s where workspace_id=%s and id=%s",
        (Jsonb({"page_token": "2", "kept": 2}), world.ws, job),
    )
    payload = {
        "connection_id": str(world.conn),
        "window_start": (now - timedelta(days=365)).isoformat(),
        "window_end": now.isoformat(),
    }
    assert post(world, "sync.backfill_chunk", job, payload) == "continued"
    state = db.execute(
        "select progress, status::text from public.job where workspace_id=%s and id=%s",
        (world.ws, job),
    ).fetchone()
    assert state is not None and state["status"] == "queued"
    assert state["progress"] == {"page_token": "2", "kept": 2}
    assert world.queue.delays == [300]
