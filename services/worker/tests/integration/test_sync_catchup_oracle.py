"""Independent oracle for Google sync catch-up (L2-05, brief cases 1-6).

Written from the original task spec requirement 4 (and
requirements 1, 5, 6) before the L2-02 implementation lands. Drives the stable
dispatch entry points (handle_incremental / handle_backfill_chunk) with the
existing fake_google.py against a bundled cluster; the only L2-02-specific
seam is LocalGoogleTokens (case 4), constructed exactly as requirement 1
describes. Skips via the one google_sync_implemented probe until that module
exists. Quote: brief case 1 "Gmail history.list 404 (expired historyId) →
exactly one full resync of the stored backfill window".
"""
# ruff: noqa: E501  (long SQL and mail fixtures, like the route twin tests)

import dataclasses
import logging
import os
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from firra.app import create_app
from firra.db.jobs import ClaimedJob
from firra.db.types import JobId, WorkspaceId
from firra.tasks.context import TaskContext
from firra.tasks.errors import PermanentTaskError
from firra.tasks.handlers.sync import handle_backfill_chunk, handle_incremental
from firra_contracts import SyncBackfillChunkPayload, SyncIncrementalPayload
from tests.conftest import local_settings
from tests.fake_google import FakeGoogle, event
from tests.integration.sync_oracle_helpers import (
    CapturingQueue,
    google_sync_implemented,
    table_count,
)
from tests.mail import email_bytes

PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not google_sync_implemented(), reason="L2-02 local tokens not implemented"),
]

FIRM = "owner@firra.local"


def _job(ws: UUID, conn: UUID, task_type: str) -> ClaimedJob:
    return ClaimedJob(
        workspace_id=WorkspaceId(ws), id=JobId(uuid4()), type=task_type, attempts=0, max_attempts=5
    )


@pytest.fixture(scope="module")
def world() -> Iterator[dict[str, Any]]:
    from fastapi.testclient import TestClient

    from firra.local.db import migrate
    from firra.local.pg import PostgresSupervisor

    if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    pg_bin = PG_BIN
    root = Path(tempfile.mkdtemp(prefix="firra-oracle-sync-", dir="/tmp"))
    pg = PostgresSupervisor(Path(pg_bin), root / "data", root / "run")
    conninfo = pg.ensure_ready()
    migrate(conninfo)
    fake = FakeGoogle()
    queue: CapturingQueue = CapturingQueue()
    app = create_app(local_settings(database_url=conninfo))
    context: TaskContext = app.state.task_context
    from firra.connectors.google.local_tokens import LocalGoogleTokens
    from firra.local.secrets import SecretStore

    local_secrets = SecretStore()
    sent_secrets: list[tuple[str, str]] = []
    revoked_conns: list[str] = []
    local_google = LocalGoogleTokens(
        fake.client(),
        local_secrets,
        client_id="cid",
        client_secret="secret",
        send_secret=lambda name, value: sent_secrets.append((name, value)),
        on_revoked=lambda connection: revoked_conns.append(str(connection.id)),
    )
    app.state.task_context = dataclasses.replace(
        context, http=fake.client(), queue=queue, google=local_google
    )
    try:
        with TestClient(app):
            with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
                ws_row = db.execute(
                    "insert into public.workspace (name) values ('Oracle') returning id"
                ).fetchone()
                assert ws_row is not None
                ws = WorkspaceId(ws_row["id"])
                clients = {}
                for name, domain, policy in (
                    ("Acme", "acme.com", "full"),
                    ("Gone", "gone.com", "excluded"),
                    ("Quiet", "quiet.com", "metadata_only"),
                ):
                    row = db.execute(
                        "insert into public.client (workspace_id, name, policy)"
                        " values (%s,%s,%s) returning id",
                        (ws, name, policy),
                    ).fetchone()
                    assert row is not None
                    clients[name] = row["id"]
                    db.execute(
                        "insert into public.client_domain (workspace_id, client_id, domain)"
                        " values (%s,%s,%s)",
                        (ws, row["id"], domain),
                    )
                conns = {}
                for provider in ("gmail", "google_calendar"):
                    row = db.execute(
                        "insert into public.connection (workspace_id, provider, mode, account_label,"
                        " external_account_id, backfill_months) values (%s,%s,'user',%s,%s,3) returning id",
                        (ws, provider, FIRM, f"g-{ws.hex[:8]}"),
                    ).fetchone()
                    assert row is not None
                    conns[provider] = row["id"]
                    local_secrets.set(f"google_refresh_token:{row['id']}", "good")
            yield {
                "app": app,
                "ctx_getter": lambda: app.state.task_context,
                "fake": fake,
                "queue": queue,
                "secrets": local_secrets,
                "sent_secrets": sent_secrets,
                "revoked_conns": revoked_conns,
                "ws": ws,
                "conns": conns,
                "clients": clients,
                "conninfo": conninfo,
                "root": root,
            }
    finally:
        pg.stop()


def _ctx(world: dict[str, Any]) -> TaskContext:
    ctx: TaskContext = world["ctx_getter"]()
    return ctx


def _incremental(world: dict[str, Any], provider: str) -> dict[str, Any]:
    ctx = _ctx(world)
    job = _job(world["ws"], world["conns"][provider], "sync.incremental")
    return handle_incremental(
        ctx, job, SyncIncrementalPayload(connection_id=world["conns"][provider])
    )


def _backfill_september(world: dict[str, Any], provider: str) -> dict[str, Any]:
    from datetime import datetime

    ctx = _ctx(world)
    job = _job(world["ws"], world["conns"][provider], "sync.backfill_chunk")
    return handle_backfill_chunk(
        ctx,
        job,
        SyncBackfillChunkPayload(
            connection_id=world["conns"][provider],
            window_start=datetime(2026, 9, 1, tzinfo=UTC),
            window_end=datetime(2026, 10, 1, tzinfo=UTC),
        ),
    )


def _gmail_ids(conninfo: str, ws: UUID) -> list[str]:
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        rows = db.execute(
            "select external_id from public.source_record where workspace_id = %s order by external_id",
            (ws,),
        ).fetchall()
        return [str(r["external_id"]) for r in rows]


def _cursor(conninfo: str, conn: UUID) -> dict[str, Any]:
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        row = db.execute("select cursor from public.connection where id = %s", (conn,)).fetchone()
        assert row is not None
        return dict(row["cursor"])


# Case 1 (L2-02 req 4): history 404 → exactly one full resync of the stored backfill window.
def test_1_expired_history_resyncs_once_without_duplicates(world: dict[str, Any]) -> None:
    fake: FakeGoogle = world["fake"]
    ws, conn, conninfo = world["ws"], world["conns"]["gmail"], world["conninfo"]
    for i in range(5):
        fake.add(
            f"acme-{i}",
            email_bytes(
                sender="ceo@acme.com",
                to=[FIRM],
                date=f"Mon, 0{i + 1} Sep 2026 10:00:00 +0000",
                message_id=f"<acme-{i}@acme.com>",
            ),
            thread_id=f"t{i}",
            record_history=True,
        )
    with psycopg.connect(conninfo, autocommit=True) as db:
        db.execute(
            "update public.connection set cursor = %s where id = %s",
            (Jsonb({"history_id": "10"}), conn),
        )

    def history_calls() -> list[str]:
        return [c for c in fake.calls if c.endswith("/history")]

    before = len(history_calls())
    _incremental(world, "gmail")
    assert len(history_calls()) == before + 1, "expected exactly one history call (the expired one)"
    assert _gmail_ids(conninfo, ws) == [f"acme-{i}" for i in range(5)]
    assert _cursor(conninfo, conn)["history_id"] == str(fake.history_id)
    assert world["fake"].watch_topics == [], "local profile must never create a Gmail watch"


# Case 2 (L2-02 req 4, S5 "3 days offline"): one sweep catches 300 missed messages up.
def test_2_offline_catch_up_is_complete_and_idempotent(world: dict[str, Any]) -> None:
    fake: FakeGoogle = world["fake"]
    ws, conninfo = world["ws"], world["conninfo"]
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        row = db.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label,"
            " external_account_id, backfill_months) values (%s,'gmail','user',%s,%s,3) returning id",
            (ws, FIRM, f"g2-{ws.hex[:8]}"),
        ).fetchone()
        assert row is not None
        offline = row["id"]
        world["secrets"].set(f"google_refresh_token:{offline}", "good")
    conn2 = offline
    world["conns"]["gmail2"] = conn2
    _incremental(world, "gmail2")
    for i in range(300):
        fake.add(
            f"bulk-{i}",
            email_bytes(
                sender="ceo@acme.com",
                to=[FIRM],
                date="Tue, 02 Sep 2026 10:00:00 +0000",
                message_id=f"<bulk-{i}@acme.com>",
            ),
            thread_id=f"bulk-{i}",
            record_history=True,
        )
    before = table_count(conninfo, "source_record")
    _incremental(world, "gmail2")
    ids = _gmail_ids(conninfo, ws)
    assert len(ids) == len(set(ids)), "duplicate source_record rows after catch-up"
    assert table_count(conninfo, "source_record") == before + 300
    _incremental(world, "gmail2")
    assert table_count(conninfo, "source_record") == before + 300, "second sweep must add nothing"


# Case 3 (L2-02 req 4): calendar 410 → full resync, no duplicates, cancelled stays cancelled.
def test_3_expired_sync_token_resyncs_calendar(world: dict[str, Any]) -> None:
    fake: FakeGoogle = world["fake"]
    ws, conn, conninfo = world["ws"], world["conns"]["google_calendar"], world["conninfo"]

    def upcoming(days: int) -> tuple[str, str]:
        def fmt(moment: datetime) -> str:
            return moment.isoformat().replace("+00:00", "Z")

        start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=days)
        end = start + timedelta(minutes=30)
        return fmt(start), fmt(end)

    start, end = upcoming(10)
    fake.put_event(event("ev-1", start, end, ["ceo@acme.com"]))
    start, end = upcoming(11)
    fake.put_event(
        event(
            "ev-2",
            start,
            end,
            ["ceo@acme.com"],
            status="cancelled",
        )
    )
    start, end = upcoming(12)
    fake.put_event(event("ev-3", start, end, ["ceo@acme.com"]))
    fake.expired_sync_tokens.add("stale-token")
    with psycopg.connect(conninfo, autocommit=True) as db:
        db.execute(
            "update public.connection set cursor = %s where id = %s",
            (Jsonb({"sync_token": "stale-token"}), conn),
        )
    result = _incremental(world, "google_calendar")
    assert result.get("sync_reset") is True
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db2:
        rows = db2.execute(
            "select r.external_id, e.status from public.calendar_event e"
            " join public.source_record r on r.id = e.source_record_id"
            " where e.workspace_id = %s order by r.external_id",
            (ws,),
        ).fetchall()
        assert [(r["external_id"], r["status"]) for r in rows] == [
            ("ev-1", "scheduled"),
            ("ev-2", "cancelled"),
            ("ev-3", "scheduled"),
        ]
        assert _cursor(conninfo, conn)["sync_token"] != "stale-token"


# Case 4 (L2-02 req 1): rotation is cached, delivered, and leaks nowhere.
def test_4_rotation_cached_delivered_and_clean(
    world: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    import importlib

    from firra.connectors.google.credentials import GMAIL_SCOPE
    from firra.local.secrets import SecretStore

    local_tokens = importlib.import_module("firra.connectors.google.local_tokens")
    tokens_cls = local_tokens.LocalGoogleTokens
    slot_for = local_tokens.token_slot

    fake: FakeGoogle = world["fake"]
    ws, conninfo = world["ws"], world["conninfo"]
    rotated = {"seen": 0}
    inner = fake.handler

    def rotating(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            rotated["seen"] += 1
            if rotated["seen"] == 1:
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-new",
                        "expires_in": 3600,
                        "refresh_token": "rotated-xyz",
                    },
                )
            return httpx.Response(200, json={"access_token": "access-new", "expires_in": 3600})
        return inner(request)

    http = httpx.Client(transport=httpx.MockTransport(rotating))
    store = SecretStore()
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        row = db.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label,"
            " external_account_id, backfill_months) values (%s,'gmail','user',%s,%s,3) returning *",
            (ws, FIRM, f"g4-{ws.hex[:8]}"),
        ).fetchone()
        assert row is not None
        from firra.db.connections import ConnectionRow

        connection = ConnectionRow(
            **{k: row[k] for k in ConnectionRow.__dataclass_fields__ if k in row}
        )
    slot = slot_for(connection)
    store.set(slot, "good")
    sent: list[tuple[str, str]] = []
    try:
        tokens = tokens_cls(
            http,
            store,
            client_id="cid",
            client_secret="secret",
            send_secret=lambda name, value: sent.append((name, value)),
            on_revoked=lambda _connection: None,
        )
    except TypeError as exc:
        pytest.fail(
            f"LocalGoogleTokens construction differs from the brief-derived assumption: {exc}"
        )
        raise
    with caplog.at_level(logging.DEBUG):
        first = tokens.access_token(connection, [GMAIL_SCOPE])
        second = tokens.access_token(connection, [GMAIL_SCOPE])
    assert first == second == "access-new"
    assert rotated["seen"] == 1, "access token must be cached until near expiry"
    assert sent == [(slot, "rotated-xyz")], "rotated token must reach the app exactly once"
    assert store.get(slot) == "rotated-xyz"
    assert "rotated-xyz" not in caplog.text
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        stored = db.execute(
            "select token_ciphertext from public.connection where id = %s", (connection.id,)
        ).fetchone()
        assert stored is not None and stored["token_ciphertext"] is None


def test_5_revoked_token_stops_sync_without_storm(world: dict[str, Any]) -> None:

    fake: FakeGoogle = world["fake"]
    ws, conninfo = world["ws"], world["conninfo"]
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        row = db.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label,"
            " external_account_id, backfill_months) values (%s,'gmail','user',%s,%s,3) returning id",
            (ws, FIRM, f"g3-{ws.hex[:8]}"),
        ).fetchone()
        assert row is not None
        revoked_conn = row["id"]
        world["secrets"].set(f"google_refresh_token:{revoked_conn}", "revoked")
    fake.revoked_refresh_tokens.add("revoked")
    try:

        def oauth_calls() -> list[str]:
            return [c for c in fake.calls if c.endswith("/token")]

        before = len(oauth_calls())
        ctx = _ctx(world)
        job = _job(ws, revoked_conn, "sync.incremental")
        with pytest.raises(PermanentTaskError) as excinfo:
            handle_incremental(ctx, job, SyncIncrementalPayload(connection_id=revoked_conn))
        assert "token_revoked" in str(excinfo.value)
        with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
            row = db.execute(
                "select status from public.connection where id = %s", (revoked_conn,)
            ).fetchone()
            assert row is not None and row["status"] == "revoked"
        with pytest.raises(PermanentTaskError):
            handle_incremental(
                ctx,
                _job(ws, revoked_conn, "sync.incremental"),
                SyncIncrementalPayload(connection_id=revoked_conn),
            )
        assert len(oauth_calls()) == before + 1, (
            "revoked connection must not hammer the token endpoint"
        )
    finally:
        fake.revoked_refresh_tokens.discard("revoked")


# Case 6 (L2-02 req 5): excluded never stored, metadata_only stores no body.
def test_6_policy_minimization_holds(world: dict[str, Any]) -> None:
    fake: FakeGoogle = world["fake"]
    ws, conninfo = world["ws"], world["conninfo"]
    fake.add(
        "gone-1",
        email_bytes(
            sender="boss@gone.com",
            to=[FIRM],
            date="Wed, 03 Sep 2026 10:00:00 +0000",
            message_id="<gone-1@gone.com>",
        ),
        thread_id="gone",
        record_history=True,
    )
    fake.add(
        "quiet-1",
        email_bytes(
            sender="soft@quiet.com",
            to=[FIRM],
            date="Wed, 03 Sep 2026 10:00:00 +0000",
            message_id="<quiet-1@quiet.com>",
        ),
        thread_id="quiet",
        record_history=True,
    )
    _backfill_september(world, "gmail")
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        gone = db.execute(
            "select count(*) from public.source_record r join public.client c on c.id = r.client_id"
            " where r.workspace_id = %s and c.name = 'Gone'",
            (ws,),
        ).fetchone()
        assert gone is not None and int(gone["count"]) == 0
        quiet = db.execute(
            "select body_clean from public.source_record r join public.client c on c.id = r.client_id"
            " where r.workspace_id = %s and c.name = 'Quiet'",
            (ws,),
        ).fetchall()
        assert quiet, "metadata_only mail must still be stored as metadata"
        assert all(r["body_clean"] is None for r in quiet)
