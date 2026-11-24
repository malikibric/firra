"""Google Calendar backfill and incremental sync: fake Calendar API, local Postgres."""

import dataclasses
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from firra.app import create_app
from firra.connectors.google.auth import GoogleAuth
from firra.connectors.google.credentials import GoogleUserTokens
from firra.db.types import WorkspaceId
from firra.tasks.context import TaskContext
from tests.conftest import LOCAL_SECRET, local_settings
from tests.fake_google import FakeGoogle, event, stored_tokens
from tests.tokens import local_token

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]
FIRM = "ana@northbeam.agency"


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
    client: TestClient
    fake: FakeGoogle


@pytest.fixture
def world(db: Db) -> Iterator[World]:
    ws = uuid4()
    user = uuid4()
    db.execute(
        "insert into auth.users (id, instance_id, aud, role, email, email_confirmed_at) values "
        "(%s, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', %s, now())",
        (user, f"ana-{ws.hex[:6]}@northbeam.agency"),
    )
    db.execute("insert into public.workspace (id, name) values (%s, 'Northbeam')", (ws,))
    db.execute(
        "insert into public.membership (workspace_id, user_id, role) values (%s, %s, 'owner')",
        (ws, user),
    )
    client_row = db.execute(
        "insert into public.client (workspace_id, name) values (%s, 'Acme') returning id", (ws,)
    ).fetchone()
    assert client_row is not None
    db.execute(
        "insert into public.client_domain (workspace_id, client_id, domain) "
        "values (%s, %s, 'acme.com')",
        (ws, client_row["id"]),
    )
    conn = db.execute(
        "insert into public.connection (workspace_id, provider, mode, account_label, "
        "external_account_id) values (%s, 'google_calendar', 'user', %s, %s) returning id",
        (ws, FIRM, f"cal-{ws.hex[:8]}"),
    ).fetchone()
    assert conn is not None

    fake = FakeGoogle()
    app = create_app(local_settings())
    context: TaskContext = app.state.task_context
    http = fake.client()
    app.state.task_context = dataclasses.replace(
        context,
        http=http,
        google=GoogleAuth(
            user=GoogleUserTokens("cid", "s", http), delegated=None, keyring=context.keyring
        ),
    )
    with TestClient(app) as client:
        blob = context.keyring.encrypt_token(WorkspaceId(ws), conn["id"], stored_tokens("good"))
        db.execute(
            "update public.connection set token_ciphertext = %s where id = %s", (blob, conn["id"])
        )
        yield World(ws=ws, conn=conn["id"], client=client, fake=fake)
    db.execute("delete from public.workspace where id = %s", (ws,))
    db.execute("delete from auth.users where id = %s", (user,))


def run(db: Db, world: World, task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    job = db.execute(
        "insert into public.job (workspace_id, type, idempotency_key, connection_id) "
        "values (%s, %s, %s, %s) returning id",
        (world.ws, task_type, f"{task_type}-{uuid4()}", world.conn),
    ).fetchone()
    assert job is not None
    response = world.client.post(
        f"/tasks/{task_type}",
        headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
        json={
            "workspace_id": str(world.ws),
            "job_id": str(job["id"]),
            "idempotency_key": "k",
            "type": task_type,
            "payload": payload,
        },
    )
    assert response.json() == {"status": "succeeded"}
    row = db.execute("select progress from public.job where id = %s", (job["id"],)).fetchone()
    assert row is not None
    return dict(row["progress"])


def statuses(db: Db, world: World) -> dict[str, str]:
    rows = db.execute(
        "select r.external_id, e.status::text as status from public.calendar_event e "
        "join public.source_record r on r.id = e.source_record_id where r.workspace_id = %s",
        (world.ws,),
    ).fetchall()
    return {r["external_id"]: r["status"] for r in rows}


def iso(dt: datetime) -> str:
    return dt.isoformat()


def test_calendar_backfill_and_incremental(db: Db, world: World) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    past, future = now - timedelta(days=3), now + timedelta(days=3)
    hour = timedelta(hours=1)
    fake = world.fake
    fake.put_event(event("held", iso(past), iso(past + hour), ["ceo@acme.com"]))
    fake.put_event(
        event("cancelled", iso(past), iso(past + hour), ["ceo@acme.com"], status="cancelled")
    )
    fake.put_event(
        event(
            "moved",
            iso(past),
            iso(past + hour),
            ["ceo@acme.com"],
            original_start=iso(past - timedelta(days=1)),
        )
    )
    fake.put_event(event("internal", iso(past), iso(past + hour), ["ben@northbeam.agency"]))
    fake.put_event(event("upcoming", iso(future), iso(future + hour), ["ceo@acme.com"]))
    all_day = event("offsite", "x", "y", ["ceo@acme.com"])
    all_day["start"], all_day["end"] = (
        {"date": past.date().isoformat()},
        {"date": now.date().isoformat()},
    )
    fake.put_event(all_day)

    backfill = run(
        db,
        world,
        "sync.backfill_chunk",
        {
            "connection_id": str(world.conn),
            "window_start": iso(now - timedelta(days=30)),
            "window_end": iso(now + timedelta(days=30)),
        },
    )
    assert (
        backfill["kept"] == 4
        and backfill["dropped"] == {"no_client": 1}
        and backfill["invalid"] == 1
    )
    assert statuses(db, world) == {
        "held": "held",
        "cancelled": "cancelled",
        "moved": "rescheduled",
        "upcoming": "scheduled",
    }
    duration = db.execute(
        "select duration_min, attendee_count from public.calendar_event e "
        "join public.source_record r on r.id = e.source_record_id "
        "where r.external_id = 'held' and r.workspace_id = %s",
        (world.ws,),
    ).fetchone()
    assert duration == {"duration_min": 60, "attendee_count": 1}

    # First incremental run: upcoming events, then a sync token.
    first = run(db, world, "sync.incremental", {"connection_id": str(world.conn)})
    assert first["sync_reset"] is False
    cursor = db.execute(
        "select cursor from public.connection where id = %s", (world.conn,)
    ).fetchone()
    assert cursor is not None and cursor["cursor"]["sync_token"] == str(len(fake.event_log))

    # The client cancels the upcoming meeting.
    fake.put_event(
        event("upcoming", iso(future), iso(future + hour), ["ceo@acme.com"], status="cancelled")
    )
    run(db, world, "sync.incremental", {"connection_id": str(world.conn)})
    assert statuses(db, world)["upcoming"] == "cancelled"

    # An expired sync token starts over cleanly.
    fake.expired_sync_tokens.add(str(len(fake.event_log)))
    reset = run(db, world, "sync.incremental", {"connection_id": str(world.conn)})
    assert reset["sync_reset"] is True
