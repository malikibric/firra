"""Suggestion run states and re-reading mail when a client becomes readable."""

from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from firra.local.routes import onboarding, setup
from firra.local.server import create_local_app
from firra_contracts import EngineStatus, TaskEnvelope
from tests.integration.conftest import LOCAL_AUTH, LOCAL_PORT

pytestmark = pytest.mark.integration


def test_suggestions_and_reread_on_client_changes(local_app: tuple[Any, Any, str]) -> None:
    _, ctx, conninfo = local_app
    ws = ctx.identity.workspace_id
    sent: list[TaskEnvelope] = []
    status = EngineStatus.model_validate(
        {
            "engine_version": "t",
            "database": "ready",
            "connections": [],
            "ai": {
                "executor": "conversation_only",
                "tools": [],
                "queue": {"pending": 0, "done": 0, "rejected_items": 0},
                "state": "conversation_only",
            },
            "relay": {"state": "offline"},
            "disk": {"data_bytes": 0},
        }
    )
    app = create_local_app(
        api_token="t" * 40,
        port=LOCAL_PORT,
        status=lambda: status,
        routers=[
            onboarding.build_router(ctx, enqueue=sent.append),
            setup.build_router(
                ctx, mail_scope_changed=lambda: onboarding.read_mail_again(ctx, sent.append)
            ),
        ],
    )
    client = TestClient(app, base_url=f"http://127.0.0.1:{LOCAL_PORT}")
    with psycopg.connect(conninfo, autocommit=True) as db:
        gmail = db.execute(
            "insert into public.connection (workspace_id, provider, mode, owner_user_id,"
            " account_label, external_account_id, backfill_months)"
            " values (%s, 'gmail', 'user', %s, 'owner@x.test', 'owner@x.test', 3) returning id",
            (ws, ctx.identity.user_id),
        ).fetchone()
        assert gmail is not None

    empty = client.get("/local/onboarding/suggestions", headers=LOCAL_AUTH).json()
    assert empty == {"state": "none", "scanned": 0, "items": [], "error_class": None} or (
        empty["state"] == "none" and empty["items"] == []
    )
    assert (
        client.post(
            "/local/onboarding/suggestions",
            json={"connection_id": str(uuid4())},
            headers=LOCAL_AUTH,
        ).status_code
        == 404
    )
    started = client.post(
        "/local/onboarding/suggestions", json={"connection_id": str(gmail[0])}, headers=LOCAL_AUTH
    )
    assert started.status_code == 202 and sent[-1].type == "onboarding.suggest"
    assert (
        client.get("/local/onboarding/suggestions", headers=LOCAL_AUTH).json()["state"] == "running"
    )
    with psycopg.connect(conninfo, autocommit=True) as db:
        db.execute(
            "update public.job set status = 'succeeded', progress = %s::jsonb where id = %s",
            (
                '{"scanned": 40, "suggestions": [{"domain": "acme.test", "thread_count": 5,'
                ' "message_count": 9, "last_seen": "2026-09-30T10:00:00+00:00",'
                ' "sample_names": ["Ana"]}]}',
                sent[-1].job_id,
            ),
        )
    ready = client.get("/local/onboarding/suggestions", headers=LOCAL_AUTH).json()
    assert ready["state"] == "ready" and ready["scanned"] == 40
    assert ready["items"][0]["domain"] == "acme.test"

    # creating a Full client with a domain re-reads mail: one backfill chunk per month, once an hour
    before = len(sent)
    created = client.post(
        f"/v1/workspaces/{ws}/clients",
        json={"name": "Acme", "domains": ["acme.test"]},
        headers=LOCAL_AUTH,
    )
    assert created.status_code == 200, created.text
    chunks = sent[before:]
    assert len(chunks) == 3 and {c.type for c in chunks} == {"sync.backfill_chunk"}
    assert {c.payload["connection_id"] for c in chunks} == {str(gmail[0])}
    # excluded clients and clients without domains do not trigger a re-read
    before = len(sent)
    client.post(
        f"/v1/workspaces/{ws}/clients",
        json={"name": "Nodomain", "domains": []},
        headers=LOCAL_AUTH,
    )
    assert len(sent) == before
    # a second client minutes later (same hour) must read history again: its mail was discarded
    keys_before = {c.idempotency_key for c in sent if c.type == "sync.backfill_chunk"}
    before = len(sent)
    second = client.post(
        f"/v1/workspaces/{ws}/clients",
        json={"name": "Beta", "domains": ["beta.test"]},
        headers=LOCAL_AUTH,
    )
    assert second.status_code == 200, second.text
    again = sent[before:]
    assert len(again) == 3 and not ({c.idempotency_key for c in again} & keys_before)
