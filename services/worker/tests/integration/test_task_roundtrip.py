"""Worker against the local Supabase Postgres (`supabase start`): task -> job row updated."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from firra.app import create_app
from tests.conftest import LOCAL_SECRET, local_settings
from tests.tokens import local_token

pytestmark = pytest.mark.integration


@pytest.fixture
def db() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    settings = local_settings()
    with psycopg.connect(
        settings.database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as conn:
        yield conn


@pytest.fixture
def workspace(db: psycopg.Connection[dict[str, Any]]) -> Iterator[UUID]:
    row = db.execute(
        "insert into public.workspace (name) values ('it-worker') returning id"
    ).fetchone()
    assert row is not None
    yield row["id"]
    db.execute("delete from public.workspace where id = %s", (row["id"],))


def create_job(db: psycopg.Connection[dict[str, Any]], workspace_id: UUID) -> UUID:
    row = db.execute(
        "insert into public.job (workspace_id, type, idempotency_key, payload) "
        "values (%s, 'diagnostics.ping', %s, '{}') returning id",
        (workspace_id, f"it-{uuid4()}"),
    ).fetchone()
    assert row is not None
    return UUID(str(row["id"]))


def post_ping(client: TestClient, workspace_id: UUID, job_id: UUID) -> Any:
    return client.post(
        "/tasks/diagnostics.ping",
        headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
        json={
            "workspace_id": str(workspace_id),
            "job_id": str(job_id),
            "idempotency_key": f"it-{job_id}",
            "type": "diagnostics.ping",
            "payload": {"requested_at": "2026-09-27T10:00:00Z"},
        },
    )


def test_ping_task_updates_job_row(db: psycopg.Connection[dict[str, Any]], workspace: UUID) -> None:
    job_id = create_job(db, workspace)
    with TestClient(create_app(local_settings())) as client:
        assert client.get("/ready").json() == {"status": "ok"}
        assert post_ping(client, workspace, job_id).json() == {"status": "succeeded"}
        assert post_ping(client, workspace, job_id).json() == {"status": "noop"}

    job = db.execute("select * from public.job where id = %s", (job_id,)).fetchone()
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["attempts"] == 1
    assert "pong_at" in job["progress"]
    assert job["finished_at"] is not None


def test_wrong_workspace_cannot_touch_job(
    db: psycopg.Connection[dict[str, Any]], workspace: UUID
) -> None:
    job_id = create_job(db, workspace)
    with TestClient(create_app(local_settings())) as client:
        assert post_ping(client, uuid4(), job_id).json() == {"status": "noop"}

    job = db.execute("select status, attempts from public.job where id = %s", (job_id,)).fetchone()
    assert job == {"status": "queued", "attempts": 0}
