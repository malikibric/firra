"""Conversation packets over MCP and direct lease state on encrypted bundled PostgreSQL."""

import hashlib
import json
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from firra.ai.executor import extraction_schema
from firra.ai.lease import LeaseError
from firra.db.local_processing import ConversationRepo
from firra.db.records import RecordInput, RecordRepo
from firra.db.types import WorkspaceId
from firra.local.bodycrypt import BodyKeyProvider, bind
from firra.local.mcp.tools_processing import ProcessingTools
from firra.local.secrets import SecretStore
from tests.integration.conftest import LOCAL_AUTH
from tests.integration.mcp_client import McpClient, is_error_result, rpc_result
from tests.unit.ai.test_processor import BODY, item

MCP_PROCESSING_TOOLS = True
pytestmark = pytest.mark.integration


@pytest.fixture
def seed(local_app: tuple[Any, Any, str]) -> Iterator[dict[str, Any]]:
    http, ctx, _ = local_app
    ws = WorkspaceId(ctx.identity.workspace_id)
    secrets = SecretStore()
    secrets.set("db_encryption_key", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAE=")
    bind(ctx.pool, BodyKeyProvider(secrets))
    with ctx.pool.connection() as conn:
        clients = {}
        for name in ("full", "flipped", "metadata", "excluded"):
            row = conn.execute(
                "insert into public.client (workspace_id,name,policy) "
                "values (%s,%s,'full') returning id",
                (ws, name),
            ).fetchone()
            assert row is not None
            clients[name] = row["id"]
        connection = conn.execute(
            "insert into public.connection "
            "(workspace_id,provider,mode,account_label) "
            "values (%s,'email_upload','upload','fixture') "
            "returning id",
            (ws,),
        ).fetchone()
        assert connection is not None
    repo = RecordRepo(ctx.pool)
    ids = {}
    bodies = {
        "newest": BODY,
        "older": BODY,
        "flipped": "We will approve the revised report before next Monday.",
        "metadata": "METADATA_PRIVATE_BODY_NEVER_IN_PACKET",
        "excluded": "EXCLUDED_PRIVATE_BODY_NEVER_IN_PACKET",
    }
    for n, (name, body) in enumerate(bodies.items()):
        record = RecordInput(
            connection_id=connection["id"],
            provider="email_upload",
            external_id=str(uuid4()),
            kind="email",
            client_id=clients.get(name, clients["full"]),
            policy="full",
            thread_key=str(uuid4()),
            occurred_at=datetime(2026, 10, 5, tzinfo=UTC) - timedelta(hours=n),
            direction="in",
            word_count=len(body.split()),
            metadata={"subject": name},
            body_clean=body,
            raw_object_uri=None,
            deep_link=None,
            content_hash=hashlib.sha256(body.encode()).digest(),
        )
        ids[name] = repo.upsert_record(
            workspace_id=ws, record=record, author_person_id=None, participants=[]
        )[0]
    with ctx.pool.connection() as conn:
        for name, policy in (("metadata", "metadata_only"), ("excluded", "excluded")):
            conn.execute(
                "update public.client set policy=%s where workspace_id=%s and id=%s",
                (policy, ws, clients[name]),
            )
    try:
        yield {"http": http, "ctx": ctx, "ws": ws, "clients": clients, "ids": ids, "bodies": bodies}
    finally:
        with ctx.pool.connection() as conn:
            conn.execute(
                "delete from public.connection where workspace_id=%s and id=%s",
                (ws, connection["id"]),
            )
            conn.execute(
                "delete from public.client where workspace_id=%s and id=any(%s)",
                (ws, list(clients.values())),
            )


def output(*results: tuple[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {"results": [{"record": handle, "items": items} for handle, items in results]}


def test_mcp_packet_submit_rejects_inventions_and_changed_policy(seed: dict[str, Any]) -> None:
    with seed["ctx"].pool.connection() as conn:
        baseline = conn.execute(
            "select count(*) as n from public.audit_log where workspace_id=%s "
            "and action in ('mcp.get_pending_mail','mcp.submit_extractions')",
            (seed["ws"],),
        ).fetchone()
        assert baseline is not None
    mcp = McpClient(seed["http"], LOCAL_AUTH)
    assert is_error_result(mcp.tools_call("get_pending_mail", {"limit": 11}))
    first = rpc_result(mcp.tools_call("get_pending_mail", {"limit": 10}))["structuredContent"]
    assert first["output_schema"] == extraction_schema()
    assert "$schema" not in first["output_schema"] and "$id" not in first["output_schema"]
    assert "UNTRUSTED" in first["instructions"].upper()
    assert BODY in first["packet"] and "enc:v1:" not in first["packet"]
    assert (
        "METADATA_PRIVATE_BODY" not in first["packet"]
        and "EXCLUDED_PRIVATE_BODY" not in first["packet"]
    )
    assert first["packet"].index("Subject: newest") < first["packet"].index("Subject: older")
    again = rpc_result(mcp.tools_call("get_pending_mail", {}))["structuredContent"]
    assert again == first
    with seed["ctx"].pool.connection() as conn:
        conn.execute(
            "update public.client set policy='excluded' where workspace_id=%s and id=%s",
            (seed["ws"], seed["clients"]["flipped"]),
        )
    args = {
        "lease_id": first["lease_id"],
        "output": output(
            ("r1", [item(kind="request")]),
            ("r2", [item(kind="request", quote="We love your work very much")]),
            ("r3", [item(kind="request", quote=seed["bodies"]["flipped"])]),
            ("r9", [item(kind="request")]),
        ),
    }
    result = rpc_result(mcp.tools_call("submit_extractions", args))["structuredContent"]
    assert result == {
        "accepted": 1,
        "rejected_by_reason": {
            "quote_not_found": 1,
            "unknown_handle": 1,
            "policy_or_record_changed": 1,
        },
        "records_done": 2,
        "records_left": 0,
    }
    assert BODY not in json.dumps(result)
    assert (
        rpc_result(mcp.tools_call("submit_extractions", {**args, "output": {}}))[
            "structuredContent"
        ]
        == result
    )
    with seed["ctx"].pool.connection() as conn:
        rows = conn.execute(
            "select source_record_id, payload, model, prompt_version from public.extraction "
            "where workspace_id=%s",
            (seed["ws"],),
        ).fetchall()
        assert len(rows) == 1 and rows[0]["source_record_id"] == seed["ids"]["newest"]
        assert rows[0]["payload"]["_provenance"]["executor"] == "conversation"
        assert (rows[0]["model"], rows[0]["prompt_version"]) == ("conversation", "extract.v6")
        audit = conn.execute(
            "select count(*) as n from public.audit_log where workspace_id=%s "
            "and action in ('mcp.get_pending_mail','mcp.submit_extractions')",
            (seed["ws"],),
        ).fetchone()
        assert audit is not None and audit["n"] == baseline["n"] + 4


def test_expiry_invalid_output_and_restart_preserve_pending(seed: dict[str, Any]) -> None:
    now = [datetime(2026, 10, 5, tzinfo=UTC)]
    ctx = replace(seed["ctx"], clock=lambda: now[0])
    tools = ProcessingTools(ctx)
    lease = tools.call("get_pending_mail", {"limit": 1})
    with pytest.raises(LeaseError, match="invalid_output"):
        tools.call(
            "submit_extractions", {"lease_id": lease["lease_id"], "output": {"private": BODY}}
        )
    now[0] += timedelta(minutes=30)
    with pytest.raises(LeaseError, match="lease_expired"):
        tools.call("submit_extractions", {"lease_id": lease["lease_id"], "output": output()})
    restarted = ProcessingTools(ctx)
    new_lease = restarted.call("get_pending_mail", {"limit": 1})
    assert new_lease["lease_id"] != lease["lease_id"] and new_lease["packet"] == lease["packet"]
    with seed["ctx"].pool.connection() as conn:
        rows = conn.execute(
            "select processing_state::text, processing_attempts from public.source_record "
            "where workspace_id=%s and id=%s",
            (seed["ws"], seed["ids"]["newest"]),
        ).fetchall()
        assert rows == [{"processing_state": "pending", "processing_attempts": 0}]


def test_cached_packet_is_not_reissued_after_policy_change(seed: dict[str, Any]) -> None:
    tools = ProcessingTools(seed["ctx"])
    lease = tools.call("get_pending_mail", {"limit": 1})
    with seed["ctx"].pool.connection() as conn:
        conn.execute(
            "update public.client set policy='metadata_only' where workspace_id=%s and id=%s",
            (seed["ws"], seed["clients"]["full"]),
        )
    replacement = tools.call("get_pending_mail", {"limit": 1})
    assert replacement["lease_id"] != lease["lease_id"] and BODY not in replacement["packet"]
    assert seed["bodies"]["flipped"] in replacement["packet"]


def test_completed_lease_requests_followup_once(seed: dict[str, Any]) -> None:
    completed: list[Any] = []
    tools = ProcessingTools(seed["ctx"], enqueue=completed.append)
    lease = tools.call("get_pending_mail", {"limit": 1})
    args = {"lease_id": lease["lease_id"], "output": output(("r1", [item(kind="request")]))}
    result = tools.call("submit_extractions", args)
    assert result["accepted"] == result["records_done"] == 1
    assert result["records_left"] == 2
    assert tools.call("submit_extractions", args) == result
    assert len(completed) == 2
    assert completed[0].job_id == completed[1].job_id
    assert completed[0].type == "memory.rebuild"
    assert completed[0].idempotency_key == f"memory:conversation:{lease['lease_id']}"
    with seed["ctx"].pool.connection() as conn:
        jobs = conn.execute(
            "select id, payload from public.job where workspace_id=%s and idempotency_key=%s",
            (seed["ws"], completed[0].idempotency_key),
        ).fetchall()
    assert len(jobs) == 1 and jobs[0]["id"] == completed[0].job_id
    assert jobs[0]["payload"] == {"trigger": "processing", "as_of": None}


def test_database_failure_rolls_back_packet_and_can_retry(
    seed: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = ProcessingTools(seed["ctx"])
    lease = tools.call("get_pending_mail", {"limit": 2})
    args = {
        "lease_id": lease["lease_id"],
        "output": output(("r1", [item(kind="request")]), ("r2", [item(kind="request")])),
    }
    original = ConversationRepo.save
    calls = 0

    def fail_second(self: ConversationRepo, **kwargs: Any) -> bool:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic_database_failure")
        return original(self, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ConversationRepo, "save", fail_second)
        with pytest.raises(RuntimeError, match="synthetic_database_failure"):
            tools.call("submit_extractions", args)
    with seed["ctx"].pool.connection() as conn:
        row = conn.execute(
            "select count(*) as n from public.extraction where workspace_id=%s", (seed["ws"],)
        ).fetchone()
        assert row is not None and row["n"] == 0
        rows = conn.execute(
            "select processing_state::text from public.source_record "
            "where workspace_id=%s and id=any(%s)",
            (seed["ws"], [seed["ids"]["newest"], seed["ids"]["older"]]),
        ).fetchall()
        assert all(row["processing_state"] == "pending" for row in rows)
    result = tools.call("submit_extractions", args)
    assert result["accepted"] == result["records_done"] == 2
    assert tools.call("submit_extractions", args) == result


def test_mcp_followup_runs_on_the_local_executor(seed: dict[str, Any], tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from firra.local.profile import LocalRuntime, build_settings
    from firra.local.routes import all_routers
    from firra.local.server import create_local_app
    from firra_contracts import EngineStatus

    secrets = SecretStore()
    secrets.set("db_encryption_key", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAE=")
    runtime = LocalRuntime(
        build_settings(seed["ctx"].pool.conninfo, tmp_path), secrets, identity=seed["ctx"].identity
    )
    ctx = replace(seed["ctx"], pool=runtime.pool)
    app = create_local_app(
        api_token="synthetic-processing-token",
        port=49152,
        status=lambda: EngineStatus.model_validate(
            seed["http"].get("/local/status", headers=LOCAL_AUTH).json()
        ),
        routers=all_routers(ctx, processing_enqueue=runtime.executor.enqueue),
    )
    runtime.executor.start()
    try:
        with TestClient(app, base_url="http://127.0.0.1:49152") as http:
            mcp = McpClient(http, {"Authorization": "Bearer synthetic-processing-token"})
            lease = rpc_result(mcp.tools_call("get_pending_mail", {"limit": 1}))[
                "structuredContent"
            ]
            args = {"lease_id": lease["lease_id"], "output": output(("r1", [item(kind="request")]))}
            result = rpc_result(mcp.tools_call("submit_extractions", args))["structuredContent"]
            assert result["accepted"] == 1
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with runtime.pool.connection() as conn:
                    job = conn.execute(
                        "select id, status::text, error_class from public.job "
                        "where workspace_id=%s and idempotency_key=%s",
                        (seed["ws"], f"memory:conversation:{lease['lease_id']}"),
                    ).fetchone()
                if job and job["status"] in ("succeeded", "failed"):
                    break
                time.sleep(0.01)
            assert job is not None and job["status"] == "succeeded"
            with runtime.pool.connection() as conn:
                followup = conn.execute(
                    "select id from public.job where workspace_id=%s "
                    "and type='signals.evaluate' and idempotency_key=%s",
                    (seed["ws"], f"signals:{job['id']}"),
                ).fetchone()
                assert followup is not None
            assert runtime.context.llm is None
            assert (
                rpc_result(mcp.tools_call("submit_extractions", args))["structuredContent"]
                == result
            )
    finally:
        runtime.stop()
