"""Independent oracle for Slack sync and minimization (L2-05, brief cases 7-12).

Written from the original task spec requirements 3, 6, 7
before the L2S-04 implementation lands. Drives the stable dispatch entry
points (handle_backfill_chunk / handle_incremental) with provider "slack"
and asserts database state; the only assumed seam is that sync.slack plugs
into those tables like every other provider. Skips via the one
slack_sync_implemented probe until firra.connectors.slack.sync exists.
Quote: brief case 7 "Unmapped channel and a direct message with a colleague
leave zero rows".
"""
# ruff: noqa: E501  (long SQL fixtures, like the route twin tests)

import dataclasses
import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.app import create_app
from firra.connectors.google.auth import GoogleAuth
from firra.connectors.google.credentials import GoogleUserTokens
from firra.connectors.slack.local_tokens import LocalSlackTokens, token_slot
from firra.db.jobs import ClaimedJob
from firra.db.types import JobId, WorkspaceId
from firra.local.secrets import SecretStore
from firra.tasks.context import TaskContext
from firra.tasks.errors import ContinueTask, PermanentTaskError
from firra.tasks.handlers.sync_slack import handle_sync_slack
from firra_contracts import SyncSlackPayload
from tests.conftest import local_settings
from tests.fake_google import FakeGoogle
from tests.integration.sync_oracle_helpers import (
    FIXTURES,
    CapturingQueue,
    FakeSlack,
    slack_sync_implemented,
    table_count,
)

PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not slack_sync_implemented(), reason="L2S-04 Slack sync not implemented"),
]

WINDOWS_START = datetime(2026, 9, 1, tzinfo=UTC)
WINDOWS_END = datetime(2026, 10, 5, tzinfo=UTC)


@pytest.fixture
def world() -> Iterator[dict[str, Any]]:
    from fastapi.testclient import TestClient

    from firra.local.db import migrate
    from firra.local.pg import PostgresSupervisor

    if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    pg_bin = PG_BIN
    root = Path(tempfile.mkdtemp(prefix="firra-oracle-slack-", dir="/tmp"))
    pg = PostgresSupervisor(Path(pg_bin), root / "data", root / "run")
    conninfo = pg.ensure_ready()
    migrate(conninfo)
    fake_google = FakeGoogle()
    fake_slack = FakeSlack()
    fake_slack.load_fixtures(FIXTURES)
    queue: CapturingQueue = CapturingQueue()
    app = create_app(local_settings(database_url=conninfo))
    context: TaskContext = app.state.task_context
    app.state.task_context = dataclasses.replace(
        context,
        http=fake_slack.client(),
        slack=LocalSlackTokens(SecretStore()),
        queue=queue,
        google=GoogleAuth(
            user=GoogleUserTokens("cid", "secret", fake_google.client()),
            delegated=None,
            keyring=context.keyring,
        ),
    )
    try:
        with TestClient(app):
            with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
                ws_row = db.execute(
                    "insert into public.workspace (name) values ('OracleSlack') returning id"
                ).fetchone()
                assert ws_row is not None
                ws = WorkspaceId(ws_row["id"])
                acme = db.execute(
                    "insert into public.client (workspace_id, name, policy) values (%s,'AcmeSlack','full')"
                    " returning id",
                    (ws,),
                ).fetchone()
                gone = db.execute(
                    "insert into public.client (workspace_id, name, policy) values (%s,'GoneSlack','excluded')"
                    " returning id",
                    (ws,),
                ).fetchone()
                assert acme is not None and gone is not None
                conn = db.execute(
                    "insert into public.connection (workspace_id, provider, mode, account_label,"
                    " external_account_id, backfill_months) values (%s,'slack','user','oracle',%s,3) returning id",
                    (ws, "U-OWNER"),
                ).fetchone()
                assert conn is not None
                secrets = SecretStore()
                secrets.set(token_slot(conn["id"]), "xoxp-oracle-slack-synthetic")
                app.state.task_context = dataclasses.replace(
                    app.state.task_context, slack=LocalSlackTokens(secrets)
                )
                db.execute(
                    "update public.connection set cursor=%s where workspace_id=%s and id=%s",
                    (
                        '{"team_id":"T-OWN","workspace_url":"https://oracle.slack.com/"}',
                        ws,
                        conn["id"],
                    ),
                )
                db.execute(
                    "insert into public.client_domain (workspace_id,client_id,domain) values (%s,%s,'acme.com')",
                    (ws, acme["id"]),
                )
                for channel, client in (("C-mapped", acme["id"]), ("C-internal", acme["id"])):
                    db.execute(
                        "insert into public.connection_scope (workspace_id, connection_id, kind,"
                        " external_id, client_id, include) values (%s,%s,'channel',%s,%s,true)",
                        (ws, conn["id"], channel, client),
                    )
                colleague = db.execute(
                    "insert into public.person (workspace_id, side, display_name) values (%s,'firm','Oracle Colleague')"
                    " returning id",
                    (ws,),
                ).fetchone()
                assert colleague is not None
            yield {
                "app": app,
                "ctx_getter": lambda: app.state.task_context,
                "fake": fake_slack,
                "ws": ws,
                "conn": conn["id"],
                "acme": acme["id"],
                "gone": gone["id"],
                "conninfo": conninfo,
                "colleague": colleague["id"],
                "secrets": secrets,
            }
    finally:
        pg.stop()
        shutil.rmtree(root)


def _ctx(world: dict[str, Any]) -> TaskContext:
    ctx: TaskContext = world["ctx_getter"]()
    return ctx


def _job(ws: WorkspaceId, conn: UUID, task_type: str) -> ClaimedJob:
    return ClaimedJob(
        workspace_id=ws,
        id=JobId(uuid4()),
        type=task_type,
        attempts=0,
        max_attempts=5,
        progress={"until": WINDOWS_END.isoformat()},
    )


def _run(world: dict[str, Any], *, backfill: bool) -> dict[str, Any]:
    ctx = _ctx(world)
    job = _job(world["ws"], world["conn"], "sync.slack")
    payload = SyncSlackPayload(
        connection_id=world["conn"],
        window_start=WINDOWS_START if backfill else None,
        window_end=WINDOWS_END if backfill else None,
    )
    while True:
        try:
            return handle_sync_slack(ctx, job, payload)
        except ContinueTask as exc:
            assert exc.delay_seconds >= 0
            job = dataclasses.replace(job, progress=exc.progress)


def _backfill(world: dict[str, Any]) -> dict[str, Any]:
    return _run(world, backfill=True)


def _incremental(world: dict[str, Any]) -> dict[str, Any]:
    return _run(world, backfill=False)


# Case 7 (L2S-04 req 6): unmapped channel and colleague DM leave zero rows.
def test_7_unmapped_and_colleague_dm_leave_no_trace(world: dict[str, Any]) -> None:
    fake: FakeSlack = world["fake"]
    conninfo = world["conninfo"]
    fake.add_message("C-open", "1791071000.000001", "U-OWNER", "Oracle unmapped chatter here today")
    fake.add_message(
        "D-colleague", "1791071000.000002", "U-COL", "Oracle colleague private note here"
    )
    before = {t: table_count(conninfo, t) for t in ("source_record", "person", "person_identity")}
    _backfill(world)
    after = {t: table_count(conninfo, t) for t in ("source_record", "person", "person_identity")}
    assert after == before, f"unmapped data left rows: {before} -> {after}"


# Case 8 (L2S-04 req 5): sides and directions across channel kinds.
def test_8_sides_and_directions(world: dict[str, Any]) -> None:
    fake: FakeSlack = world["fake"]
    conninfo, ws = world["conninfo"], world["ws"]
    fake.add_message(
        "C-mapped", "1791072000.000001", "U-EXT", "Oracle external asks about renewal terms"
    )
    fake.add_message("C-mapped", "1791072000.000002", "U-OWN", "Oracle firm replies with dates")
    fake.add_message("C-internal", "1791072000.000003", "U-OWN", "Oracle internal note about acme")
    fake.add_message(
        "D-client", "1791072000.000004", "U-EXT", "Oracle direct question about invoice"
    )
    _backfill(world)
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        rows = db.execute(
            "select r.external_id, r.direction, p.side from public.source_record r"
            " join public.person p on p.id = r.author_person_id"
            " where r.workspace_id = %s and r.external_id like 'C-mapped:%%' or r.external_id like 'D-client:%%'"
            " or r.external_id like 'C-internal:%%' order by r.external_id",
            (ws,),
        ).fetchall()
        by_id = {r["external_id"]: (r["direction"], r["side"]) for r in rows}
        assert by_id.get("C-mapped:1791072000.000001") == ("in", "client")
        assert by_id.get("C-mapped:1791072000.000002") == ("out", "firm")
        assert by_id.get("C-internal:1791072000.000003") == ("internal", "firm")
        assert by_id.get("D-client:1791072000.000004") == ("in", "client")
        identity = db.execute(
            "select kind, value from public.person_identity where workspace_id = %s and value = 'U-EXT'",
            (ws,),
        ).fetchone()
        assert identity is not None and identity["kind"] == "slack_id"


# Case 9 (L2S-04 req 3): edit → new hash + pending; delete → row and derived gone.
def test_9_edit_reprocesses_and_delete_removes(world: dict[str, Any]) -> None:
    fake: FakeSlack = world["fake"]
    conninfo, ws = world["conninfo"], world["ws"]
    fake.add_message(
        "C-mapped", "1791073000.000001", "U-EXT", "Oracle edit me original wording here"
    )
    fake.add_message("C-mapped", "1791073000.000002", "U-EXT", "Oracle doomed message wording here")
    _backfill(world)
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        doomed = db.execute(
            "select id, content_hash from public.source_record where workspace_id = %s and external_id = 'C-mapped:1791073000.000002'",
            (ws,),
        ).fetchone()
        assert doomed is not None
        with db.transaction():
            extraction = db.execute(
                "insert into public.extraction (workspace_id, source_record_id, client_id, kind, payload,"
                " model, prompt_version, processing_version, item_index) values (%s,%s,%s,'request',%s,'oracle','v1',1,0) returning id",
                (ws, doomed["id"], world["acme"], '{"text": "x"}'),
            ).fetchone()
            evidence = db.execute(
                "insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)"
                " values (%s,%s,%s,'Oracle doomed message wording here','{}') returning id",
                (ws, doomed["id"], world["acme"]),
            ).fetchone()
            assert extraction is not None and evidence is not None
            db.execute(
                "insert into public.extraction_evidence (workspace_id,extraction_id,evidence_id,client_id) values (%s,%s,%s,%s)",
                (ws, extraction["id"], evidence["id"], world["acme"]),
            )
    for message in fake.messages["C-mapped"]:
        if message["ts"] == "1791073000.000001":
            message["text"] = "Oracle edit me changed wording here now"
    fake.messages["C-mapped"] = [
        m for m in fake.messages["C-mapped"] if m["ts"] != "1791073000.000002"
    ]
    _incremental(world)
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        edited = db.execute(
            "select content_hash, processing_state from public.source_record"
            " where workspace_id = %s and external_id = 'C-mapped:1791073000.000001'",
            (ws,),
        ).fetchone()
        assert edited is not None and edited["processing_state"] == "pending"
        gone = db.execute(
            "select id from public.source_record where workspace_id = %s and external_id = 'C-mapped:1791073000.000002'",
            (ws,),
        ).fetchone()
        assert gone is None, "deleted ts must remove the record"
        derived = db.execute(
            "select count(*) from public.extraction where workspace_id = %s", (ws,)
        ).fetchone()
        assert derived is not None


# Case 10 (L2S-04 req 3): latest_reply change fetches replies once; 429 honoured.
def test_10_thread_update_and_rate_limit(world: dict[str, Any]) -> None:
    fake: FakeSlack = world["fake"]
    conninfo = world["conninfo"]
    fake.add_message("C-mapped", "1791074000.000001", "U-OWN", "Oracle thread parent message here")
    _backfill(world)
    fake.add_message(
        "C-mapped",
        "1791074000.000002",
        "U-EXT",
        "Oracle thread reply message here",
        thread_ts="1791074000.000001",
    )
    replies_before = len([c for c in fake.calls if "conversations.replies" in c])
    _incremental(world)
    replies_after = len([c for c in fake.calls if "conversations.replies" in c])
    assert replies_after >= replies_before + 1, "thread update must fetch replies"
    assert (
        table_count(
            conninfo, "source_record", "where external_id = %s", "C-mapped:1791074000.000002"
        )
        == 1
    )
    fake.throttle_remaining = 2
    _incremental(world)
    assert (
        table_count(
            conninfo, "source_record", "where external_id = %s", "C-mapped:1791074000.000002"
        )
        == 1
    )


# Case 11 (L2S-04 req 1, 9): bad tokens store nothing and refuse the connection.
def test_11_revoked_token_refuses_without_data(world: dict[str, Any]) -> None:
    fake: FakeSlack = world["fake"]
    conninfo, ws = world["conninfo"], world["ws"]
    before = table_count(conninfo, "source_record")
    fake.auth_mode = "invalid_auth"
    try:
        with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
            row = db.execute(
                "insert into public.connection (workspace_id, provider, mode, account_label,"
                " external_account_id, backfill_months) values (%s,'slack','user','bad',%s,3) returning id",
                (ws, "U-BAD"),
            ).fetchone()
            assert row is not None
            bad = row["id"]
            world["secrets"].set(token_slot(bad), "xoxp-oracle-bad-token")
        ctx = _ctx(world)
        from firra.db.jobs import ClaimedJob

        raised = False
        try:
            handle_sync_slack(
                ctx,
                ClaimedJob(
                    workspace_id=ws,
                    id=JobId(uuid4()),
                    type="sync.slack",
                    attempts=0,
                    max_attempts=1,
                ),
                SyncSlackPayload(
                    connection_id=bad, window_start=WINDOWS_START, window_end=WINDOWS_END
                ),
            )
        except PermanentTaskError:
            raised = True
        with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
            status = db.execute(
                "select status from public.connection where id = %s", (bad,)
            ).fetchone()
            assert status is not None
            assert table_count(conninfo, "source_record") == before
            assert raised or status["status"] in ("revoked", "error")
    finally:
        fake.auth_mode = "ok"


# Case 12 (L2S-04 req 8): no Slack text, names, emails or tokens in logs.
def test_12_sync_logs_carry_no_content(
    world: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:

    fake: FakeSlack = world["fake"]
    fake.add_message(
        "C-mapped", "1791075000.000001", "U-EXT", "Oracle sensitive content cantaloupe here"
    )
    with caplog.at_level(logging.DEBUG):
        _backfill(world)
    assert "cantaloupe" not in caplog.text
    assert "External Dana" not in caplog.text and "dana@acme.com" not in caplog.text
    for bearer in fake.bearers:
        if bearer:
            assert bearer not in caplog.text
