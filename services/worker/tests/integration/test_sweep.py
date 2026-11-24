"""Scheduled sweep: one incremental sync per active connection per slot."""

import dataclasses
import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from firra.app import create_app
from tests.conftest import LOCAL_SECRET, local_settings
from tests.tokens import local_token

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]


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


@pytest.fixture
def connections(db: Db) -> Iterator[dict[str, UUID]]:
    ws = db.execute("insert into public.workspace (name) values ('sweep') returning id").fetchone()
    assert ws is not None
    ids: dict[str, UUID] = {}
    for key, provider, mode, status, token in (
        ("gmail", "gmail", "user", "active", "v1.x.y"),
        ("revoked", "gmail", "user", "revoked", None),
        ("upload", "email_upload", "upload", "active", None),
    ):
        row = db.execute(
            "insert into public.connection (workspace_id, provider, mode, status, account_label, "
            "external_account_id, token_ciphertext) values (%s, %s, %s, %s, %s, %s, %s) "
            "returning id",
            (ws["id"], provider, mode, status, key, None if mode == "upload" else key, token),
        ).fetchone()
        assert row is not None
        ids[key] = row["id"]
    yield ids
    db.execute("delete from public.workspace where id = %s", (ws["id"],))


def test_sweep_enqueues_active_mail_connections_once_per_slot(connections: dict[str, UUID]) -> None:
    queue = CapturingQueue()
    app = create_app(local_settings())
    app.state.task_context = dataclasses.replace(app.state.task_context, queue=queue)
    headers = {"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"}
    with TestClient(app) as client:
        assert client.post("/maintenance/sweep").status_code == 401
        client.post("/maintenance/sweep", headers=headers)
        swept = {
            str(e.payload["connection_id"]) for e in queue.envelopes if e.type == "sync.incremental"
        }
        assert str(connections["gmail"]) in swept
        assert str(connections["revoked"]) not in swept
        assert str(connections["upload"]) not in swept

        queue.envelopes.clear()
        client.post("/maintenance/sweep", headers=headers)
        again = {
            str(e.payload["connection_id"]) for e in queue.envelopes if e.type == "sync.incremental"
        }
        assert str(connections["gmail"]) not in again, "same slot -> no duplicate job"


def test_sweep_resumes_long_jobs_that_lost_their_task(db: Db, connections: dict[str, UUID]) -> None:
    conn = db.execute(
        "select workspace_id from public.connection where id = %s", (connections["gmail"],)
    ).fetchone()
    assert conn is not None
    ws = conn["workspace_id"]
    jobs: dict[str, UUID] = {}
    # updated_at is set by a trigger on every write; switch it off to age the jobs.
    db.execute("alter table public.job disable trigger job_set_updated_at")
    try:
        for name, job_type, status, idle in (
            ("stuck", "sync.backfill_chunk", "queued", "2 hours"),
            ("dead_run", "sync.backfill_chunk", "running", "45 minutes"),
            ("waiting", "sync.backfill_chunk", "queued", "3 minutes"),
            ("failed", "sync.backfill_chunk", "failed", "2 hours"),
            ("incremental", "sync.incremental", "queued", "2 hours"),
        ):
            row = db.execute(
                "insert into public.job (workspace_id, type, status, idempotency_key, payload, "
                "connection_id, updated_at) values (%s, %s, %s, %s, %s, %s, now() - %s::interval) "
                "returning id",
                (
                    ws,
                    job_type,
                    status,
                    f"backfill:{name}#cont-0123456789ab",
                    json.dumps({"connection_id": str(connections["gmail"])}),
                    connections["gmail"],
                    idle,
                ),
            ).fetchone()
            assert row is not None
            jobs[name] = row["id"]
    finally:
        db.execute("alter table public.job enable trigger job_set_updated_at")

    queue = CapturingQueue()
    app = create_app(local_settings())
    app.state.task_context = dataclasses.replace(app.state.task_context, queue=queue)
    headers = {"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"}
    with TestClient(app) as client:
        client.post("/maintenance/sweep", headers=headers)
        resumed = {e.job_id: e for e in queue.envelopes if e.type == "sync.backfill_chunk"}
        assert set(resumed) == {jobs["stuck"], jobs["dead_run"]}
        key = resumed[jobs["stuck"]].idempotency_key
        assert key.startswith("backfill:stuck#cont-") and key.count("#cont-") == 1

        queue.envelopes.clear()
        client.post("/maintenance/sweep", headers=headers)
        assert not [e for e in queue.envelopes if e.type == "sync.backfill_chunk"]
