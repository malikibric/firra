"""Diagnostics contain no client content; backup travels through the API and restores."""

import base64
import json
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from tests.integration.conftest import LOCAL_AUTH

pytestmark = pytest.mark.integration

SECRET_NAME = "Zephyr Secret Client"
SECRET_MAIL = "founder.secret@zephyr-client.test"
SECRET_QUOTE = "We will cancel unless the invoice dispute ends"


def test_diagnostics_are_content_free(local_app: tuple[Any, Any, str]) -> None:
    client, ctx, conninfo = local_app
    ws = ctx.identity.workspace_id
    with psycopg.connect(conninfo, autocommit=True) as db:
        cid = db.execute(
            "insert into public.client (workspace_id, name, policy) values (%s, %s, 'full')"
            " returning id",
            (ws, SECRET_NAME),
        ).fetchone()
        assert cid is not None
        db.execute(
            "insert into public.client_domain (workspace_id, client_id, domain)"
            " values (%s, %s, 'zephyr-client.test')",
            (ws, cid[0]),
        )
        db.execute(
            "insert into public.connection (workspace_id, provider, mode, owner_user_id,"
            " account_label, external_account_id, status, last_error_class)"
            " values (%s, 'gmail', 'user', %s, %s, %s, 'error', 'token_revoked')",
            (ws, ctx.identity.user_id, SECRET_MAIL, SECRET_MAIL),
        )
        conn_id = db.execute(
            "select id from public.connection where account_label = %s", (SECRET_MAIL,)
        ).fetchone()
        assert conn_id is not None
        db.execute(
            "insert into public.source_record (workspace_id, client_id, connection_id, provider,"
            " kind, direction, policy_at_ingest, external_id, occurred_at, body_clean)"
            " values (%s,%s,%s,'gmail','email','in','full',%s,now(),%s)",
            (ws, cid[0], conn_id[0], str(uuid4()), SECRET_QUOTE),
        )
    res = client.post("/local/diagnostics/export", headers=LOCAL_AUTH)
    assert res.status_code == 200, res.text
    text = res.text
    for secret in (SECRET_NAME, SECRET_MAIL, SECRET_QUOTE, "zephyr", str(cid[0]), str(ws)):
        assert secret not in text
    body = res.json()
    assert {
        "provider": "gmail",
        "kind": "email",
        "processing_state": "not_applicable",
        "count": 1,
    } in body["records"]
    assert any(c["last_error_class"] == "token_revoked" for c in body["connections"])
    assert body["migrations"] > 0 and body["engine_version"] == ctx.engine_version


def test_backup_roundtrip_wrong_phrase_and_non_empty(local_app: tuple[Any, Any, str]) -> None:
    client = local_app[0]
    created = client.post("/local/backup/create", headers=LOCAL_AUTH)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["filename"].endswith(".firrabackup") and body["recovery_phrase"]
    assert SECRET_NAME.encode() not in base64.b64decode(body["backup_b64"])
    wrong = client.post(
        "/local/backup/restore",
        json={"backup_b64": body["backup_b64"], "phrase": "WRONG-PHRASE"},
        headers=LOCAL_AUTH,
    )
    assert wrong.status_code == 422 and wrong.json()["error"] == "invalid_backup"
    garbage = client.post(
        "/local/backup/restore",
        json={"backup_b64": "!!!not base64!!!", "phrase": body["recovery_phrase"]},
        headers=LOCAL_AUTH,
    )
    assert garbage.status_code == 422
    # this workspace already has data (earlier tests of the module): restore is refused
    busy = client.post(
        "/local/backup/restore",
        json={"backup_b64": body["backup_b64"], "phrase": body["recovery_phrase"]},
        headers=LOCAL_AUTH,
    )
    assert busy.status_code == 409 and busy.json()["error"] == "workspace_not_empty"
    assert json.loads(busy.text)["error"]


def test_metrics_export_is_content_free_and_counts_important_cards(
    local_app: tuple[Any, Any, str],
) -> None:
    client, ctx, conninfo = local_app
    ws = ctx.identity.workspace_id
    with psycopg.connect(conninfo, autocommit=True) as db:
        cid = db.execute(
            "insert into public.client (workspace_id, name, policy) values (%s, %s, 'full')"
            " returning id",
            (ws, SECRET_NAME),
        ).fetchone()
        assert cid is not None
        for n in range(2):
            db.execute(
                "insert into public.decision_card (workspace_id, client_id, week, kind, title,"
                " body, locale, score, priority, inputs, model, prompt_version, dedupe_key,"
                " play, facts, interpretation, recommendation, strength, importance,"
                " surfaced_on, reason_code)"
                " values (%s,%s,date_trunc('week', current_date - 14)::date,'risk',%s,"
                "'PRIVATE BODY','en',1,1,'{}',"
                "'fixture','fixture',%s,'monitor',%s,'x','y','weak','important',"
                "current_date - 14,'state_change')",
                (ws, cid[0], SECRET_QUOTE, f"m{n}", json.dumps([{"basis": "metadata"}])),
            )
    res = client.post("/local/metrics/export", headers=LOCAL_AUTH)
    assert res.status_code == 200, res.text
    for secret in (SECRET_NAME, SECRET_QUOTE, "PRIVATE BODY", str(cid[0]), str(ws)):
        assert secret not in res.text
    body = res.json()
    assert sum(w["count"] for w in body["important_per_week"]) == 2
    assert body["engine_version"] == ctx.engine_version and "workspace_id" not in body
