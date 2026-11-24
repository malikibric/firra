"""ingest.upload end to end on the local Supabase Postgres: minimization and policies hold."""

import dataclasses
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from firra.app import create_app
from firra.storage.raw import LocalRawStore
from tests.conftest import LOCAL_SECRET, local_settings
from tests.mail import email_bytes, mbox_bytes
from tests.tokens import local_token

pytestmark = pytest.mark.integration


class CapturingQueue:
    def __init__(self) -> None:
        self.envelopes: list[Any] = []
        self.delays: list[float] = []

    def enqueue(self, envelope: Any, *, delay_seconds: float = 0) -> None:
        self.envelopes.append(envelope)
        self.delays.append(delay_seconds)


Db = psycopg.Connection[dict[str, Any]]


@pytest.fixture
def db() -> Iterator[Db]:
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as conn:
        yield conn


@pytest.fixture
def world(db: Db) -> Iterator[dict[str, UUID]]:
    """Northbeam (firm) with clients Acme (full), Globex (metadata_only), Umbrella (excluded)."""
    user_id, ws = uuid4(), uuid4()
    db.execute(
        "insert into auth.users (id, instance_id, aud, role, email, email_confirmed_at) "
        "values (%s, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', "
        "%s, now())",
        (user_id, f"ana-{ws.hex[:6]}@northbeam.agency"),
    )
    db.execute("insert into public.workspace (id, name) values (%s, 'Northbeam')", (ws,))
    db.execute(
        "insert into public.membership (workspace_id, user_id, role) values (%s, %s, 'owner')",
        (ws, user_id),
    )
    ids: dict[str, UUID] = {"ws": ws}
    for key, name, policy, domain in (
        ("acme", "Acme", "full", "acme.com"),
        ("globex", "Globex", "metadata_only", "globex.com"),
        ("umbrella", "Umbrella", "excluded", "umbrella.org"),
    ):
        row = db.execute(
            "insert into public.client (workspace_id, name, policy) "
            "values (%s, %s, %s) returning id",
            (ws, name, policy),
        ).fetchone()
        assert row is not None
        ids[key] = row["id"]
        db.execute(
            "insert into public.client_domain (workspace_id, client_id, domain) "
            "values (%s, %s, %s)",
            (ws, row["id"], domain),
        )
    row = db.execute(
        "insert into public.connection (workspace_id, provider, mode, account_label) "
        "values (%s, 'email_upload', 'upload', 'export.mbox') returning id",
        (ws,),
    ).fetchone()
    assert row is not None
    ids["conn"] = row["id"]
    yield ids
    db.execute("delete from public.workspace where id = %s", (ws,))
    db.execute("delete from auth.users where id = %s", (user_id,))


def upload(world: dict[str, UUID], data: bytes) -> str:
    settings = local_settings()
    assert settings.local_raw_dir
    name = f"uploads/{world['ws']}/{world['conn']}/export.mbox"
    return LocalRawStore(Path(settings.local_raw_dir)).put(name, data, "application/mbox")


def run_task(db: Db, world: dict[str, UUID], uri: str) -> dict[str, Any]:
    job = db.execute(
        "insert into public.job (workspace_id, type, idempotency_key, connection_id) "
        "values (%s, 'ingest.upload', %s, %s) returning id",
        (world["ws"], f"upload-{uuid4()}", world["conn"]),
    ).fetchone()
    assert job is not None
    with TestClient(create_app(local_settings())) as client:
        response = client.post(
            "/tasks/ingest.upload",
            headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
            json={
                "workspace_id": str(world["ws"]),
                "job_id": str(job["id"]),
                "idempotency_key": "k",
                "type": "ingest.upload",
                "payload": {
                    "connection_id": str(world["conn"]),
                    "object_uri": uri,
                    "format": "mbox",
                },
            },
        )
    assert response.json() == {"status": "succeeded"}
    row = db.execute(
        "select status, progress from public.job where id = %s", (job["id"],)
    ).fetchone()
    assert row is not None and row["status"] == "succeeded"
    return dict(row["progress"])


def test_upload_stores_only_client_mail_under_client_policies(
    db: Db, world: dict[str, UUID]
) -> None:
    firm = "ana@northbeam.agency"
    mails = [
        email_bytes(
            sender="ceo@acme.com",
            to=[firm],
            subject="Q3 goals",
            body=(
                "Revenue up 20%\n\n"
                "On Tue, Sep 1, 2026 at 10:00 AM Ana <ana@northbeam.agency> wrote:\n"
                "> quoted history is never stored"
            ),
            message_id="<acme-1@acme.com>",
        ),
        email_bytes(
            sender=firm,
            to=["pm@globex.com"],
            subject="Secret roadmap",
            body="Private",
            message_id="<globex-1@northbeam.agency>",
        ),
        email_bytes(sender="news@vendor.com", to=[firm], message_id="<vendor-1@vendor.com>"),
        email_bytes(
            sender=firm,
            to=["x@acme.com"],
            cc=["boss@umbrella.org"],
            message_id="<mixed-1@northbeam.agency>",
        ),
        email_bytes(
            sender=firm,
            to=["ben@northbeam.agency"],
            subject="Acme lunch",
            message_id="<internal-1@northbeam.agency>",
        ),
        email_bytes(sender="ceo@acme.com", to=[firm], date=None, message_id="<nodate@acme.com>"),
        email_bytes(
            sender="ceo@acme.com",
            to=[firm],
            subject="Q3 goals",
            body=(
                "Revenue up 20%\n\n"
                "On Tue, Sep 1, 2026 at 10:00 AM Ana <ana@northbeam.agency> wrote:\n"
                "> quoted history is never stored"
            ),
            message_id="<acme-1@acme.com>",
        ),
    ]
    progress = run_task(db, world, upload(world, mbox_bytes(mails)))
    assert progress == {
        "processed": 7,
        "kept": 2,
        "updated": 1,
        "invalid": 1,
        "dropped": {"excluded_client": 1, "no_client": 2},
        "offset": None,
    }

    records = {
        r["external_id"]: r
        for r in db.execute(
            "select * from public.source_record where workspace_id = %s", (world["ws"],)
        ).fetchall()
    }
    assert set(records) == {"<acme-1@acme.com>", "<globex-1@northbeam.agency>"}

    acme = records["<acme-1@acme.com>"]
    assert acme["client_id"] == world["acme"] and acme["policy_at_ingest"] == "full"
    assert acme["direction"] == "in" and acme["body_clean"] == "Revenue up 20%"
    assert acme["word_count"] == 3
    assert acme["metadata"]["subject"] == "Q3 goals"
    settings = local_settings()
    assert settings.local_raw_dir
    raw_path = Path(settings.local_raw_dir) / acme["raw_object_uri"].removeprefix("gs://local/")
    assert raw_path.exists()

    globex = records["<globex-1@northbeam.agency>"]
    assert globex["policy_at_ingest"] == "metadata_only" and globex["direction"] == "out"
    assert globex["body_clean"] is None and globex["raw_object_uri"] is None
    assert globex["content_hash"] is None and "subject" not in globex["metadata"]
    assert globex["word_count"] == 1

    stored = {
        r["value"]
        for r in db.execute(
            "select value from public.person_identity where workspace_id = %s", (world["ws"],)
        ).fetchall()
    }
    # Only people from kept records, and only client or firm people.
    assert stored == {"ceo@acme.com", "ana@northbeam.agency", "pm@globex.com"}

    # Re-running the same upload converges (idempotent upserts).
    again = run_task(db, world, upload(world, mbox_bytes(mails)))
    assert again["kept"] == 0 and again["updated"] == 3
    count = db.execute(
        "select count(*) as n from public.source_record where workspace_id = %s", (world["ws"],)
    ).fetchone()
    assert count is not None and count["n"] == 2


def test_client_signature_title_is_captured_before_body_is_discarded(
    db: Db, world: dict[str, UUID]
) -> None:
    message = email_bytes(
        sender="Victor Hale <victor@acme.com>",
        to=["ana@northbeam.agency"],
        body="Please review the new plan.\n\n-- \nVictor Hale\nVP Marketing, Acme\n+1 555 010 0100",
        message_id="<title@acme.com>",
    )
    run_task(db, world, upload(world, mbox_bytes([message])))
    row = db.execute(
        """select p.role_title, p.is_decision_maker, r.body_clean
             from public.source_record r join public.person p
               on p.workspace_id = r.workspace_id and p.id = r.author_person_id
            where r.workspace_id = %s and r.external_id = '<title@acme.com>'""",
        (world["ws"],),
    ).fetchone()
    assert row is not None
    assert row["role_title"] == "VP Marketing"
    assert row["is_decision_maker"] is True
    assert row["body_clean"] == "Please review the new plan."
    observed = db.execute(
        """select o.source::text as source, o.role_title, o.is_decision_maker,
                  o.observed_at = r.occurred_at as at_record_time
             from public.person_observation o join public.source_record r
               on r.workspace_id = o.workspace_id and r.author_person_id = o.person_id
            where o.workspace_id = %s and r.external_id = '<title@acme.com>'""",
        (world["ws"],),
    ).fetchall()
    assert observed == [
        {
            "source": "signature",
            "role_title": "VP Marketing",
            "is_decision_maker": True,
            "at_record_time": True,
        }
    ]


def test_upload_outside_the_connection_prefix_is_rejected(db: Db, world: dict[str, UUID]) -> None:
    job = db.execute(
        "insert into public.job (workspace_id, type, idempotency_key) "
        "values (%s, 'ingest.upload', %s) returning id",
        (world["ws"], f"bad-{uuid4()}"),
    ).fetchone()
    assert job is not None
    other = f"gs://local/uploads/{uuid4()}/{world['conn']}/export.mbox"
    with TestClient(create_app(local_settings())) as client:
        response = client.post(
            "/tasks/ingest.upload",
            headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
            json={
                "workspace_id": str(world["ws"]),
                "job_id": str(job["id"]),
                "idempotency_key": "k",
                "type": "ingest.upload",
                "payload": {
                    "connection_id": str(world["conn"]),
                    "object_uri": other,
                    "format": "mbox",
                },
            },
        )
    assert response.json() == {"status": "failed"}
    row = db.execute("select error_class from public.job where id = %s", (job["id"],)).fetchone()
    assert row is not None and row["error_class"] == "invalid_object"


def test_large_uploads_continue_where_they_stopped(db: Db, world: dict[str, UUID]) -> None:
    messages = [
        email_bytes(
            sender="ceo@acme.com",
            to=["ana@northbeam.agency"],
            message_id=f"<m{i}@acme.com>",
        )
        for i in range(5)
    ]
    uri = upload(world, mbox_bytes(messages))
    job = db.execute(
        "insert into public.job (workspace_id, type, idempotency_key, connection_id) "
        "values (%s, 'ingest.upload', %s, %s) returning id",
        (world["ws"], f"upload-{uuid4()}", world["conn"]),
    ).fetchone()
    assert job is not None
    app = create_app(local_settings(task_time_budget_seconds=0.001))
    queue = CapturingQueue()
    app.state.task_context = dataclasses.replace(app.state.task_context, queue=queue)
    envelope = {
        "workspace_id": str(world["ws"]),
        "job_id": str(job["id"]),
        "idempotency_key": "k",
        "type": "ingest.upload",
        "payload": {"connection_id": str(world["conn"]), "object_uri": uri, "format": "mbox"},
    }
    rounds = 0
    with TestClient(app) as client:
        while True:
            status = client.post(
                "/tasks/ingest.upload",
                headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
                json=envelope,
            ).json()["status"]
            if status != "continued":
                break
            rounds += 1
            envelope["idempotency_key"] = queue.envelopes.pop().idempotency_key
    assert status == "succeeded" and rounds == 4, "one message per round with a tiny budget"
    row = db.execute("select progress from public.job where id = %s", (job["id"],)).fetchone()
    assert row is not None
    assert row["progress"]["kept"] == 5 and row["progress"]["offset"] is None
