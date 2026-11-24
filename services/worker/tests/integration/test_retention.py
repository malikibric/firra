"""Event-driven body purge on the local Postgres."""

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


@pytest.fixture
def db() -> Iterator[Db]:
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as conn:
        yield conn


@pytest.fixture
def records(db: Db) -> Iterator[dict[str, UUID]]:
    ws = db.execute(
        "insert into public.workspace (name) values ('retention') returning id"
    ).fetchone()
    assert ws is not None
    client = db.execute(
        "insert into public.client (workspace_id, name) values (%s, 'Acme') returning id",
        (ws["id"],),
    ).fetchone()
    conn = db.execute(
        "insert into public.connection (workspace_id, provider, mode, account_label) "
        "values (%s, 'email_upload', 'upload', 'x') returning id",
        (ws["id"],),
    ).fetchone()
    assert client is not None and conn is not None
    ids: dict[str, UUID] = {}
    for key, age_days in (("old", 16), ("fresh", 1)):
        row = db.execute(
            "insert into public.source_record (workspace_id, connection_id, provider, "
            "external_id, kind, client_id, policy_at_ingest, occurred_at, direction, body_clean, "
            "raw_object_uri, created_at, updated_at, processing_state) "
            "values (%s, %s, 'email_upload', %s, "
            "'email', %s, 'full', now(), 'in', 'body', 'gs://b/raw/x', "
            "now() - make_interval(days => %s), now() - interval '1 hour', 'pending') "
            "returning id",
            (ws["id"], conn["id"], key, client["id"], age_days),
        ).fetchone()
        assert row is not None
        ids[key] = row["id"]
    yield ids
    db.execute("delete from public.workspace where id = %s", (ws["id"],))


def test_purge_removes_expired_bodies_only(db: Db, records: dict[str, UUID]) -> None:
    with TestClient(create_app(local_settings())) as client:
        unauthenticated = client.post("/maintenance/purge-bodies")
        assert unauthenticated.status_code == 401
        response = client.post(
            "/maintenance/purge-bodies",
            headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
        )
    assert response.status_code == 200
    assert response.json()["purged"] >= 1
    assert response.json()["purged_unprocessed"] >= 1

    rows = {
        r["id"]: r
        for r in db.execute(
            "select id, body_clean, raw_object_uri, body_purged_at, processing_state::text "
            "from public.source_record "
            "where id = any(%s)",
            (list(records.values()),),
        ).fetchall()
    }
    old, fresh = rows[records["old"]], rows[records["fresh"]]
    assert old["body_clean"] is None and old["raw_object_uri"] is None
    assert old["body_purged_at"] is not None
    assert old["processing_state"] == "skipped_purged", "purge never waits for extraction"
    assert fresh["processing_state"] == "pending"
    assert fresh["body_clean"] == "body" and fresh["raw_object_uri"] == "gs://b/raw/x"
