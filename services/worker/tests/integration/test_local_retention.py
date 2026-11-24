"""Local retention: old text goes, derived facts stay; the owner's period applies at once."""
# ruff: noqa: E501  (long SQL fixtures)

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.integration.conftest import LOCAL_AUTH

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


def test_retention_deletes_old_text_keeps_facts_and_validates(
    local_app: tuple[Any, Any, str],
) -> None:
    client, ctx, conninfo = local_app
    ws = ctx.identity.workspace_id
    cid = uuid4()
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        db.execute(
            "insert into public.client (id, workspace_id, name, policy) values (%s,%s,'Ret','full')",
            (cid, ws),
        )
        conn = db.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label) values (%s,'email_upload','upload','r') returning id",
            (ws,),
        ).fetchone()
        assert conn is not None

        def record(days: int, state: str) -> Any:
            row = db.execute(
                "insert into public.source_record (workspace_id, connection_id, provider, external_id, client_id, kind,"
                " direction, policy_at_ingest, occurred_at, body_clean, metadata, processing_state)"
                " values (%s,%s,'email_upload',%s,%s,'email','in','full',%s,'old text','{\"subject\": \"old subject\", \"x\": 1}',%s)"
                " returning id",
                (ws, conn["id"], str(uuid4()), cid, ctx.clock() - timedelta(days=days), state),
            ).fetchone()
            assert row is not None
            return row["id"]

        old_done, old_pending, recent = (
            record(400, "done"),
            record(500, "pending"),
            record(20, "pending"),
        )
        ev = db.execute(
            "insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator) values (%s,%s,%s,'old text quote here now','{\"start\":0,\"end\":8}') returning id",
            (ws, old_done, cid),
        ).fetchone()
        assert ev is not None

    assert client.get("/local/retention", headers=LOCAL_AUTH).json() == {"body_months": 12}
    for bad in (
        {"body_months": 0},
        {"body_months": 121},
        {"body_months": True},
        {"body_months": "12"},
        {},
        {"body_months": 6, "x": 1},
    ):
        assert client.put("/local/retention", json=bad, headers=LOCAL_AUTH).status_code == 422
    assert (
        client.put("/local/retention", json={"body_months": 12}, headers=LOCAL_AUTH).status_code
        == 200
    )

    with psycopg.connect(conninfo, row_factory=dict_row) as db:
        rows = {
            r["id"]: r
            for r in db.execute(
                "select id, body_clean, body_purged_at, metadata, processing_state::text as st from public.source_record where client_id = %s",
                (cid,),
            )
        }
        assert rows[old_done]["body_clean"] is None and rows[old_done]["body_purged_at"] is not None
        assert rows[old_done]["metadata"] == {"x": 1} and rows[old_done]["st"] == "done"
        assert (
            rows[old_pending]["st"] == "skipped_purged" and rows[old_pending]["body_clean"] is None
        )
        assert (
            rows[recent]["body_clean"] == "old text"
            and rows[recent]["metadata"]["subject"] == "old subject"
        )
        kept = db.execute(
            "select count(*) as n from public.evidence where id = %s", (ev["id"],)
        ).fetchone()
        assert kept is not None and kept["n"] == 1

    # shortening applies at once; null keeps everything that is left
    assert client.put("/local/retention", json={"body_months": 1}, headers=LOCAL_AUTH).json() == {
        "body_months": 1
    }
    with psycopg.connect(conninfo, row_factory=dict_row) as db:
        still = db.execute(
            "select body_clean from public.source_record where id = %s", (recent,)
        ).fetchone()
        assert still is not None and still["body_clean"] == "old text"
    assert client.put(
        "/local/retention", json={"body_months": None}, headers=LOCAL_AUTH
    ).json() == {"body_months": None}
    client.put("/local/retention", json={"body_months": 12}, headers=LOCAL_AUTH)
