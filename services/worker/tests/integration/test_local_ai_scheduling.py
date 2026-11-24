"""Fake Google sync -> encrypted records -> fake owner CLI -> free follow-up chain."""

import dataclasses
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import SecretStr

from firra.ai.executor import ExecutorNotSignedIn
from firra.ai.local import AiRuntime
from firra.connectors.google.local_tokens import LocalGoogleTokens
from firra.db.jobs import PgJobStore
from firra.local.profile import LocalRuntime, build_settings
from firra.local.readctx import ReadContext
from firra.local.routes import all_routers
from firra.local.routes.connections import build_router as connections_router
from firra.local.secrets import SecretStore
from firra.local.server import create_local_app
from firra.tasks.execute import execute_envelope
from firra.tasks.handlers.process import request_processing
from firra_contracts import EngineStatus
from tests.fake_google import FakeGoogle, event
from tests.integration.test_ai_processor import world as ai_world
from tests.integration.test_local_google_sync import wait_for
from tests.integration.test_local_routes import AUTH
from tests.integration.test_local_routes import served as routes_served
from tests.unit.ai.test_processor import answer, item
from tests.unit.ai.test_scheduling import ProbedFake, settings
from tests.unit.local.test_executor import context

pytestmark = pytest.mark.integration
world = ai_world
served = routes_served


def test_synced_records_drain_through_the_local_free_chain_and_settings_routes(
    served: tuple[TestClient, ReadContext],
    tmp_path: Path,
) -> None:
    initial_http, initial = served
    secrets = SecretStore()
    secrets.set("db_encryption_key", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAE=")
    config = build_settings(cast(str, initial.pool.conninfo), tmp_path).model_copy(
        update={
            "google_client_id": "synthetic-desktop",
            "google_client_secret": SecretStr("synthetic-secret"),
        }
    )
    runtime = LocalRuntime(config, secrets, identity=initial.identity)
    fake = FakeGoogle()
    runtime.http.close()
    runtime.http = fake.client()
    google = runtime.context.google
    assert isinstance(google, LocalGoogleTokens)
    google._client = runtime.http
    runtime.context = dataclasses.replace(runtime.context, http=runtime.http)
    runtime.executor.context = runtime.context
    ai = runtime.ai
    assert ai is not None
    now = datetime.now(UTC)
    body = "The delivery is late. We need the attribution report before the next launch."
    fake_cli = ProbedFake(
        [
            answer(
                (
                    "r1",
                    [
                        item(
                            kind="complaint",
                            quote=body,
                            speaker="ceo@acme.com",
                            text="The delivery is late.",
                            subtype="delay",
                        ),
                        item(kind="request", quote=body, speaker="ceo@acme.com"),
                    ],
                ),
            )
        ]
    )
    ai.claude = lambda model: fake_cli
    ws = initial.identity.workspace_id
    with runtime.pool.connection() as db:
        client = db.execute(
            "insert into public.client (workspace_id,name,policy) "
            "values (%s,'Synthetic AI client','full') returning id",
            (ws,),
        ).fetchone()
        assert client is not None
        db.execute(
            "insert into public.client_domain (workspace_id,client_id,domain) "
            "values (%s,%s,'acme.com')",
            (ws, client["id"]),
        )
    fake.add("synthetic-mail", email_bytes_for_ai(body, now))
    fake.put_event(
        event(
            "synthetic-meeting",
            (now + timedelta(minutes=30)).isoformat(),
            (now + timedelta(minutes=60)).isoformat(),
            ["owner@firra.local", "ceo@acme.com"],
        )
    )
    status = EngineStatus.model_validate(initial_http.get("/local/status", headers=AUTH).json())
    app = create_local_app(
        api_token="t" * 40,
        port=49152,
        status=lambda: status.model_copy(update={"ai": ai.status()}),
        routers=all_routers(
            dataclasses.replace(initial, pool=runtime.pool, home=tmp_path),
            processing_enqueue=runtime.executor.enqueue,
            connection_router=connections_router(
                dataclasses.replace(initial, pool=runtime.pool),
                secrets=secrets,
                tasks=runtime.context,
                send_secret_delete=lambda *args: None,
            ),
        ),
    )
    runtime.executor.start()
    try:
        with TestClient(app, base_url="http://127.0.0.1:49152") as http:
            assert (
                http.get("/local/ai/settings", headers=AUTH).json()["executor"]
                == "conversation_only"
            )
            bad = http.put(
                "/local/ai/settings",
                headers=AUTH,
                json={"executor": "claude_code", "backfill_months": 3},
            )
            assert (
                bad.status_code == 422
                and bad.json()["error"] == "background_acknowledgement_required"
            )
            bad = http.put(
                "/local/ai/settings",
                headers=AUTH,
                json={"executor": "codex", "backfill_months": 3, "background_acknowledged": True},
            )
            assert bad.status_code == 422 and bad.json()["error"] == "codex_not_available"
            bad = http.put(
                "/local/ai/settings",
                headers=AUTH,
                json={"executor": "conversation_only", "backfill_months": "3"},
            )
            assert bad.status_code == 422
            root_id = uuid4()
            secrets.set(f"google_refresh_token:{root_id}", "synthetic-refresh")
            response = http.post(
                "/local/connections/google",
                headers=AUTH,
                json={
                    "connection_id": str(root_id),
                    "account_label": "owner@firra.local",
                    "backfill_months": 3,
                },
            )
            assert response.status_code == 200

            def sync_done() -> bool:
                with runtime.pool.connection() as db:
                    row = db.execute(
                        "select count(*) as n from public.source_record where workspace_id=%s",
                        (ws,),
                    ).fetchone()
                    active = db.execute(
                        "select count(*) as n from public.job where workspace_id=%s "
                        "and status in ('queued','running')",
                        (ws,),
                    ).fetchone()
                    return bool(row and row["n"] == 2 and active and active["n"] == 0)

            wait_for(sync_done)
            assert http.get("/local/ai/status", headers=AUTH).json()["queue"]["pending"] == 1
            selected = settings(batch_records=40, model="fixture-model")
            assert (
                http.put(
                    "/local/ai/settings",
                    headers=AUTH,
                    json=selected.model_dump(mode="json", exclude_none=True),
                ).status_code
                == 200
            )
            assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600
            assert request_processing(runtime.context, ai.workspace_id, "sweep")

            def chain_done() -> bool:
                with runtime.pool.connection() as db:
                    routines = db.execute(
                        "select count(*) as n from public.routine_run where workspace_id=%s "
                        "and status='succeeded'",
                        (ws,),
                    ).fetchone()
                    active = db.execute(
                        "select count(*) as n from public.job where workspace_id=%s "
                        "and status in ('queued','running')",
                        (ws,),
                    ).fetchone()
                    return bool(routines and routines["n"] == 2 and active and active["n"] == 0)

            wait_for(chain_done)
            assert runtime.context.llm is None
            assert len(fake_cli.calls) == 1
            packet = fake_cli.calls[0][1]
            assert body in packet and "enc:v1:" not in packet
            with runtime.pool.connection() as db:
                for table in ("client_card", "request", "signal", "account_assessment"):
                    row = db.execute(
                        sql.SQL("select count(*) as n from public.{} where workspace_id=%s").format(
                            sql.Identifier(table)
                        ),
                        (ws,),
                    ).fetchone()
                    assert row and row["n"] > 0, table
                cards = db.execute(
                    "select model,prompt_version,routine_run_id from public.decision_card "
                    "where workspace_id=%s",
                    (ws,),
                ).fetchall()
                assert cards and any(c["routine_run_id"] is not None for c in cards)
                assert all(
                    (c["model"], c["prompt_version"]) == ("deterministic", "facts.v1")
                    for c in cards
                )
                briefs = db.execute(
                    "select model,prompt_version,content from public.meeting_brief "
                    "where workspace_id=%s",
                    (ws,),
                ).fetchall()
                assert briefs and all(
                    (b["model"], b["prompt_version"]) == ("deterministic", "facts.v1")
                    for b in briefs
                )
                assert any(b["content"].get("unresolved_complaints") for b in briefs)
                assert (
                    db.execute(
                        "select id from public.llm_usage where workspace_id=%s", (ws,)
                    ).fetchall()
                    == []
                )
                failed = db.execute(
                    "select type,error_class from public.job where workspace_id=%s "
                    "and status='failed'",
                    (ws,),
                ).fetchall()
                assert failed == []
            ai_status = http.get("/local/status", headers=AUTH).json()["ai"]
            assert ai_status["queue"] == {"pending": 0, "done": 1, "rejected_items": 0}
            assert ai_status["state"] == "idle"
            assert (
                http.post("/local/ai/probe", headers=AUTH).json()["tools"][0]["signed_in"] is True
            )
    finally:
        runtime.stop()


def email_bytes_for_ai(body: str, now: datetime) -> bytes:
    from tests.mail import email_bytes

    return email_bytes(
        sender="ceo@acme.com",
        to=["owner@firra.local"],
        body=body,
        date=format_datetime(now - timedelta(hours=1)),
        message_id="<ai-fixture@acme.com>",
    )


def test_failed_signin_blocks_restart_sweep_but_changed_settings_resume(
    world: dict[str, Any],
    tmp_path: Path,
) -> None:
    pool, ws = world["pool"], world["ws"]
    fake = ProbedFake([ExecutorNotSignedIn()])
    ai = AiRuntime(pool, ws, tmp_path, claude=lambda model: fake)
    ai.update(settings())
    queue: list[Any] = []
    ctx = dataclasses.replace(
        context(PgJobStore(pool)),
        pool=pool,
        processing_enabled=True,
        queue=type("Queue", (), {"enqueue": lambda _, envelope: queue.append(envelope)})(),
    )
    assert request_processing(ctx, ws, "sweep")
    result = execute_envelope(ctx.jobs, ctx, queue[-1], requeue=lambda *args: None)
    assert result.status == "failed" and ai.status().state.value == "needs_signin"
    assert request_processing(ctx, ws, "sweep") is False
    restarted = AiRuntime(pool, ws, tmp_path, claude=lambda model: fake)
    assert request_processing(ctx, ws, "sweep") is False
    restarted.update(settings(model="new-model"))
    assert request_processing(ctx, ws, "sweep")
    assert len(queue) == 2
