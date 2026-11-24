"""Engine-wide log hygiene (L8-06): a full local run must never log content.

Exercises, on the bundled Postgres with all logging captured from the first
line: cluster start + migrations, Google sync through the fake, MCP
get_evidence and get_pending_mail/submit_extractions, backup create,
diagnostics export, and a Claude Code executor run against the fake CLI.
Every captured record is searched for fixture quotes, addresses, client names,
tokens and the recovery phrase. A leak is reported by this test with the
offending line (production code is not touched here).
"""

import base64
import dataclasses
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.app import create_app
from firra.db.jobs import ClaimedJob
from firra.db.types import JobId, WorkspaceId
from firra.tasks.handlers.sync import handle_backfill_chunk
from firra_contracts import SyncBackfillChunkPayload
from tests.conftest import local_settings
from tests.fake_google import FakeGoogle
from tests.mail import email_bytes

pytestmark = pytest.mark.integration
PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")

CLIENT_NAME = "Zephyr Hygiene Client"
CLIENT_MAIL = "owner.hygiene@zephyr-client.test"
CLIENT_DOMAIN = "zephyr-client.test"
QUOTE = "We will cancel unless the invoice dispute ends this quarter"
GOOGLE_REFRESH = "oracle-refresh-token-9f3a7c2e"
FIRM = "owner@firra.local"


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.name} {record.getMessage()} {record.args!r}")


def test_full_engine_run_logs_no_content(tmp_path: Path) -> None:
    if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    from fastapi.testclient import TestClient

    from firra.connectors.google.local_tokens import LocalGoogleTokens
    from firra.local.bodycrypt import BodyKeyProvider, bind
    from firra.local.bootstrap import ensure_local_owner
    from firra.local.db import migrate
    from firra.local.pg import PostgresSupervisor
    from firra.local.readctx import ReadContext
    from firra.local.routes import all_routers
    from firra.local.secrets import SecretStore
    from firra.local.server import create_local_app
    from firra_contracts import EngineStatus

    capture = Capture()
    root_logger = logging.getLogger()
    root_logger.addHandler(capture)
    old_level = root_logger.level
    root_logger.setLevel(logging.DEBUG)
    try:
        root = Path(tempfile.mkdtemp(prefix="firra-oracle-hygiene-", dir="/tmp"))
        pg = PostgresSupervisor(Path(str(PG_BIN)), root / "data", root / "run")
        conninfo = pg.ensure_ready()
        migrate(conninfo)
        fake = FakeGoogle()
        store = SecretStore()
        db_key_b64 = base64.b64encode(os.urandom(32)).decode()
        store.set("db_encryption_key", db_key_b64)
        task_app = create_app(local_settings(database_url=conninfo))
        task_context = task_app.state.task_context
        local_google = LocalGoogleTokens(
            fake.client(),
            store,
            client_id="cid",
            client_secret="secret",
            send_secret=lambda name, value: None,
            on_revoked=lambda connection: None,
        )
        task_app.state.task_context = dataclasses.replace(
            task_context, http=fake.client(), google=local_google
        )
        identity = ensure_local_owner(conninfo, timezone="UTC")
        from firra.db.pool import create_pool_for

        pool = create_pool_for(conninfo, min_size=1, max_size=4)
        pool.open()
        from firra.local.settings import OwnerSettings, save

        save(OwnerSettings(mcp_processing_tools=True), root)
        read_ctx = ReadContext(pool=pool, identity=identity, home=root)
        status = EngineStatus.model_validate(
            {
                "engine_version": "oracle",
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
        local_api = create_local_app(
            api_token="t" * 40, port=49153, status=lambda: status, routers=all_routers(read_ctx)
        )
        ws = identity.workspace_id
        with (
            TestClient(task_app),
            TestClient(local_api, base_url="http://127.0.0.1:49153") as local_http,
        ):
            with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
                client = db.execute(
                    "insert into public.client (workspace_id, name, policy)"
                    " values (%s,%s,'full') returning id",
                    (ws, CLIENT_NAME),
                ).fetchone()
                assert client is not None
                db.execute(
                    "insert into public.client_domain (workspace_id, client_id, domain)"
                    " values (%s,%s,%s)",
                    (ws, client["id"], CLIENT_DOMAIN),
                )
                conn = db.execute(
                    "insert into public.connection (workspace_id, provider, mode, account_label,"
                    " external_account_id, backfill_months) values (%s,'gmail','user',%s,%s,3)"
                    " returning id",
                    (ws, FIRM, f"h-{ws.hex[:8]}"),
                ).fetchone()
                assert conn is not None
                store.set(f"google_refresh_token:{conn['id']}", GOOGLE_REFRESH)
                person = db.execute(
                    "insert into public.person (workspace_id, client_id, side, display_name)"
                    " values (%s,%s,'client','Hygiene Author') returning id",
                    (ws, client["id"]),
                ).fetchone()
                assert person is not None
            bind(pool, BodyKeyProvider(store))
            bind(task_app.state.task_context.pool, BodyKeyProvider(store))
            fake.add(
                "hyg-1",
                email_bytes(
                    sender=CLIENT_MAIL,
                    to=[FIRM],
                    date="Tue, 02 Sep 2026 10:00:00 +0000",
                    message_id="<hyg-1@zephyr-client.test>",
                    body=f"Hello, {QUOTE}. Regards.",
                ),
                thread_id="hyg",
                record_history=True,
            )
            job = ClaimedJob(
                workspace_id=WorkspaceId(ws),
                id=JobId(uuid4()),
                type="sync.backfill_chunk",
                attempts=0,
                max_attempts=5,
            )
            handle_backfill_chunk(
                task_app.state.task_context,
                job,
                SyncBackfillChunkPayload(
                    connection_id=conn["id"],
                    window_start=datetime(2026, 9, 1, tzinfo=UTC),
                    window_end=datetime(2026, 10, 1, tzinfo=UTC),
                ),
            )
            phrase = _mcp_backup_and_diagnostics(local_http, ws, client["id"], conninfo)
            _run_executor(tmp_path)
            needles = [
                QUOTE,
                CLIENT_MAIL,
                CLIENT_NAME,
                CLIENT_DOMAIN,
                GOOGLE_REFRESH,
                db_key_b64,
                phrase,
            ]
            text = "\n".join(capture.lines)
            assert text, "expected log records from the run"
            for needle in needles:
                assert needle not in text, f"log leak: {needle!r} in captured logs"
            for line in capture.lines:
                if "zephyr" in line.lower():
                    raise AssertionError(f"log leak, zephyr content: {line[:300]}")
            pool.close()
    finally:
        pg.stop()
        shutil.rmtree(root, ignore_errors=True)
        root_logger.removeHandler(capture)
        root_logger.setLevel(old_level)


def _tool_json(envelope: Any) -> Any:
    from tests.integration.mcp_client import rpc_result

    result = rpc_result(envelope)
    if isinstance(result, dict) and isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    if isinstance(result, dict) and isinstance(result.get("content"), list) and result["content"]:
        first = result["content"][0]
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            try:
                return json.loads(first["text"])
            except ValueError:
                pass
    return result


def _mcp_backup_and_diagnostics(local_http: Any, ws: UUID, client_id: UUID, conninfo: str) -> str:
    from tests.integration.conftest import LOCAL_AUTH
    from tests.integration.mcp_client import McpClient, rpc_result

    mcp = McpClient(local_http, LOCAL_AUTH)
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        record = db.execute(
            "select id from public.source_record where workspace_id = %s limit 1", (ws,)
        ).fetchone()
        assert record is not None
        evidence = db.execute(
            "insert into public.evidence (workspace_id, client_id, source_record_id,"
            " quote, locator, deep_link) values (%s,%s,%s,%s,'{\"start\":0}','https://example.test/x')"
            " returning id",
            (ws, client_id, record["id"], QUOTE),
        ).fetchone()
        assert evidence is not None
        evidence_id = str(evidence["id"])
    gotten = rpc_result(mcp.tools_call("get_evidence", {"evidence_id": evidence_id}))
    assert QUOTE in json.dumps(gotten)
    pending = _tool_json(mcp.tools_call("get_pending_mail", {}))
    packet = pending.get("packet", "")
    match = re.search(
        r'id="(r\d+)"[^>]*>.*?' + re.escape(QUOTE[:20]), json.dumps(packet), re.DOTALL
    )
    handle = match.group(1) if match else "r1"
    submitted = _tool_json(
        mcp.tools_call(
            "submit_extractions",
            {
                "lease_id": pending["lease_id"],
                "output": {
                    "results": [
                        {
                            "record": handle,
                            "items": [
                                {
                                    "kind": "request",
                                    "quote": QUOTE,
                                    "speaker": CLIENT_MAIL,
                                    "text": "Oracle hygiene item.",
                                    "subtype": None,
                                    "tone": None,
                                    "owner_side": None,
                                    "due_date": None,
                                    "metric": None,
                                    "change": None,
                                    "person_name": None,
                                    "role_title": None,
                                    "budget_direction": None,
                                    "initiative_type": None,
                                    "topic_key": "oracle-hygiene",
                                }
                            ],
                        }
                    ]
                },
            },
        )
    )
    assert submitted["accepted"] >= 1
    created = local_http.post("/local/backup/create", headers=LOCAL_AUTH)
    assert created.status_code == 200, created.text
    phrase = created.json()["recovery_phrase"]
    assert isinstance(phrase, str) and len(phrase) >= 8
    exported = local_http.post("/local/diagnostics/export", headers=LOCAL_AUTH)
    assert exported.status_code == 200, exported.text
    return phrase


def _run_executor(tmp_path: Path) -> None:
    import json as json_lib
    import sys as sys_mod

    from firra.ai.claude_code import ClaudeCodeExecutor

    contract = json_lib.loads(
        (
            Path(__file__).resolve().parents[4]
            / "packages"
            / "contracts"
            / "schemas"
            / "local"
            / "ai_extraction_output.json"
        ).read_text()
    )
    schema = {key: value for key, value in contract.items() if key not in ("$schema", "$id")}
    source = Path(__file__).resolve().parent.parent / "fixtures" / "fake_claude.py"
    binary = tmp_path / "claude"
    binary.write_text(f"#!{sys_mod.executable}\n" + source.read_text())
    binary.chmod(0o700)
    (binary.parent / "scenario.json").write_text(json_lib.dumps({"name": "success"}))
    prompt = f"Extract facts. {QUOTE}. From {CLIENT_MAIL}."
    packet = f'<source_record id="r1">Hello, {QUOTE}. Regards.</source_record>'
    result = ClaudeCodeExecutor(binary).run(prompt, packet, schema, timeout_seconds=30)
    assert result is not None
