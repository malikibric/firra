"""Stored read adapters, confirmed atomic writes and reloaded evidence context."""

from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from firra.local.settings import OwnerSettings, save
from tests.integration.conftest import LOCAL_AUTH
from tests.integration.mcp_client import McpClient, rpc_result
from tests.integration.test_local_mcp_oracle import seed as oracle_seed
from tests.unit.test_local_evidence_builders import NARRATIVE

pytestmark = pytest.mark.integration
seed = oracle_seed


@pytest.fixture
def mcp(local_app: tuple[Any, Any, str]) -> McpClient:
    return McpClient(local_app[0], LOCAL_AUTH)


@pytest.mark.parametrize(
    "name",
    [
        "get_clients_needing_attention",
        "list_clients",
        "get_recent_changes",
        "get_client_state",
        "get_risks",
        "get_commitments",
        "get_stakeholders",
        "get_client_unknowns",
        "get_client_timeline",
        "search_evidence",
        "get_churn_replay",
        "get_decisions",
        "get_client_context",
        "prepare_for_client",
        "get_opportunities",
    ],
)
def test_each_read_returns_validated_stored_data(
    name: str, mcp: McpClient, seed: dict[str, Any]
) -> None:
    client_tools = {
        "get_client_state",
        "get_risks",
        "get_commitments",
        "get_stakeholders",
        "get_client_unknowns",
        "get_client_timeline",
        "search_evidence",
        "get_client_context",
        "prepare_for_client",
    }
    args = {"client_id": str(seed["ids"]["full"])} if name in client_tools else {}
    result = rpc_result(mcp.tools_call(name, args))
    assert not result.get("isError"), (name, result)
    assert "structuredContent" in result
    if name == "search_evidence":
        assert len(result["structuredContent"]["items"]) == 2
    if name == "get_commitments":
        assert len(result["structuredContent"]["items"]) == 1
    if name == "prepare_for_client":
        body = result["structuredContent"]
        assert body["facts"] and "questions" not in body
        assert all("fact_id" in f and "evidence_ids" in f for f in body["facts"])


def test_context_setting_is_reloaded(
    mcp: McpClient, seed: dict[str, Any], local_app: tuple[Any, Any, str]
) -> None:
    home = local_app[1].home
    args = {"evidence_id": str(seed["evidence"]["full"]), "with_context": True}
    assert rpc_result(mcp.tools_call("get_evidence", args))["isError"]
    save(OwnerSettings(mcp_source_access=True), home)
    result = rpc_result(mcp.tools_call("get_evidence", args))
    assert not result.get("isError")
    assert result["structuredContent"]["untrusted_context"].startswith("Body ")
    save(OwnerSettings(), home)
    assert rpc_result(mcp.tools_call("get_evidence", args))["isError"]


def test_confirmed_feedback_and_outcome(mcp: McpClient, seed: dict[str, Any]) -> None:
    for name, args in [
        ("record_feedback", {"card_id": str(seed["card"]["full"]), "verdict": "useful"}),
        (
            "record_outcome",
            {
                "client_id": str(seed["ids"]["full"]),
                "event": "renewed",
                "occurred_at": "2026-10-01",
            },
        ),
    ]:
        result = rpc_result(mcp.tools_call(name, {**args, "user_confirmed": True}))
        assert not result.get("isError"), result
        assert result["structuredContent"]["id"]


def test_write_audit_failure_rolls_back(mcp: McpClient, seed: dict[str, Any]) -> None:
    with psycopg.connect(seed["conninfo"], autocommit=True) as conn:
        before_row = conn.execute(
            "select count(*) from public.outcome where workspace_id=%s", (seed["ws"],)
        ).fetchone()
        assert before_row is not None
        before = before_row[0]
        conn.execute(
            "create function public.reject_mcp_write() returns trigger language plpgsql as $$ "
            "begin if new.actor_type='mcp' then raise exception 'private'; end if; return new; "
            "end $$"
        )
        conn.execute(
            "create trigger reject_mcp_write before insert on public.audit_log for each row "
            "execute function public.reject_mcp_write()"
        )
        try:
            result = rpc_result(
                mcp.tools_call(
                    "record_outcome",
                    {
                        "client_id": str(seed["ids"]["full"]),
                        "event": "expanded",
                        "occurred_at": "2026-10-01",
                        "user_confirmed": True,
                    },
                )
            )
            assert result["isError"]
            after = conn.execute(
                "select count(*) from public.outcome where workspace_id=%s", (seed["ws"],)
            ).fetchone()
            assert after is not None and after[0] == before
        finally:
            conn.execute("drop trigger reject_mcp_write on public.audit_log")
            conn.execute("drop function public.reject_mcp_write()")


@pytest.fixture(scope="module")
def replay_seed(seed: dict[str, Any], local_app: tuple[Any, Any, str]) -> dict[str, Any]:
    with psycopg.connect(seed["conninfo"], autocommit=True, row_factory=dict_row) as conn:
        foreign = conn.execute(
            "insert into public.workspace(name) values ('MCP foreign') returning id"
        ).fetchone()
        assert foreign is not None
        conn.execute(
            "insert into public.membership(workspace_id,user_id,role) values (%s,%s,'owner')",
            (foreign["id"], local_app[1].identity.user_id),
        )
        conn.execute(
            "insert into public.client(id,workspace_id,name) values (%s,%s,'MCP foreign client')",
            (seed["ids"]["foreign"], foreign["id"]),
        )
        run = conn.execute(
            "insert into public.replay_run(workspace_id, window_start, window_end, status, "
            "finished_at) values (%s, '2026-09-01', '2026-09-30', 'succeeded', now()) "
            "returning id",
            (seed["ws"],),
        ).fetchone()
        assert run is not None
        narrative = deepcopy(NARRATIVE)
        narrative.update(visible_at_detection=[], detection_timing=[], transitions=[], events=[])
        for key in ("full", "metadata"):
            conn.execute(
                "insert into public.replay_narrative(workspace_id, replay_run_id, client_id, "
                "version, narrative) values (%s, %s, %s, 1, %s)",
                (seed["ws"], run["id"], seed["ids"][key], Jsonb(narrative)),
            )
    return {**seed, "run_id": run["id"]}


def test_ts_policy_workspace_and_audit_twins(mcp: McpClient, replay_seed: dict[str, Any]) -> None:
    seed = replay_seed
    names = (
        "get_client_state",
        "get_risks",
        "get_commitments",
        "get_stakeholders",
        "get_client_unknowns",
        "get_client_timeline",
        "search_evidence",
        "get_churn_replay",
    )
    with psycopg.connect(seed["conninfo"]) as conn:
        before = conn.execute(
            "select count(*) from public.audit_log where workspace_id=%s and actor_type='mcp'",
            (seed["ws"],),
        ).fetchone()
        assert before is not None
    successful = 0
    for name in names:
        extra = {"run_id": str(seed["run_id"])} if name == "get_churn_replay" else {}
        for key in ("full", "metadata"):
            result = rpc_result(mcp.tools_call(name, {**extra, "client_id": str(seed["ids"][key])}))
            assert not result.get("isError"), (name, result)
            successful += 1
            if key == "metadata":
                assert "untrusted_quote" not in str(result) and "Ignore previous" not in str(result)
        for client_id in (seed["ids"]["foreign"], seed["ids"]["excluded"], uuid4()):
            result = rpc_result(mcp.tools_call(name, {**extra, "client_id": str(client_id)}))
            assert result["isError"] and result["content"][0]["text"] == "Client not found."
    for name in (
        "get_clients_needing_attention",
        "list_clients",
        "get_decisions",
        "get_churn_replay",
    ):
        result = rpc_result(mcp.tools_call(name, {}))
        assert not result.get("isError")
        successful += 1
        assert "MCP foreign" not in str(result) and "Oracle Excluded" not in str(result)
        assert rpc_result(mcp.tools_call(name, {"workspace_id": str(uuid4())}))["isError"]
    with psycopg.connect(seed["conninfo"], row_factory=dict_row) as conn:
        rows = conn.execute(
            "select actor_id,  action,  target_id,  metadata from public.audit_log where "
            "workspace_id=%s and actor_type='mcp' order by created_at, id",
            (seed["ws"],),
        ).fetchall()
        assert len(rows) - before[0] == successful
        for row in rows:
            assert set(row["metadata"]) == {"count", "via"}
            assert row["target_id"] in (None, seed["ids"]["full"], seed["ids"]["metadata"])


def test_growth_stored_service_match_and_expansion(mcp: McpClient, seed: dict[str, Any]) -> None:
    with psycopg.connect(seed["conninfo"], row_factory=dict_row) as conn, conn.transaction():
        service = conn.execute(
            "insert into public.workspace_service(workspace_id, name, description) values "
            "(%s, 'Synthetic service', 'Synthetic scope') returning id",
            (seed["ws"],),
        ).fetchone()
        assert service is not None
        need = conn.execute(
            "insert into public.latent_need(workspace_id, client_id, cluster_key, "
            "workspace_service_id, mention_count, thread_count, first_seen_at, "
            "last_seen_at, rule_version) values (%s, %s, 'synthetic', %s, 3, 3, now(), "
            "now(), 'fixture') returning id",
            (seed["ws"], seed["ids"]["full"], service["id"]),
        ).fetchone()
        assert need is not None
        conn.execute(
            "insert into public.latent_need_evidence(workspace_id, client_id, "
            "latent_need_id, evidence_id) values (%s, %s, %s, %s)",
            (seed["ws"], seed["ids"]["full"], need["id"], seed["evidence"]["full"]),
        )
        assessment = conn.execute(
            "insert into public.account_assessment(workspace_id, client_id, as_of, "
            "run_kind, assessment_version, rule_version, risk_state, state_since, "
            "expansion_state, strength, drivers) values (%s, %s, current_date, 'live', "
            "'fixture', 'fixture', 'high', current_date, 'strong', 'strong', '[]') "
            "returning id",
            (seed["ws"], seed["ids"]["full"]),
        ).fetchone()
        assert assessment is not None
    result = rpc_result(
        mcp.tools_call("get_opportunities", {"client_id": str(seed["ids"]["full"])})
    )
    assert not result.get("isError"), result
    body = result["structuredContent"]
    assert body["expansions"][0]["fact_id"] == str(assessment["id"])
    assert body["expansions"][0]["expansion_state"] == "strong"
    assert body["latent_needs"][0]["service_id"] == str(service["id"])
    assert body["latent_needs"][0]["evidence_ids"] == [str(seed["evidence"]["full"])]
    assert "untrusted_quote" not in str(body)


def test_encrypted_context_custom_home_and_missing_key(
    seed: dict[str, Any],
    local_app: tuple[Any, Any, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64
    import os
    from dataclasses import replace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from firra.db.pool import create_pool_for
    from firra.local.bodycrypt import BodyKeyProvider, bind, write_body
    from firra.local.mcp.server import build_server, engine_handler
    from firra.local.mcp.transport import http_router
    from firra.local.secrets import SecretStore

    monkeypatch.setenv("FIRRA_HOME", str(tmp_path / "wrong-home"))
    home = tmp_path / "explicit-home"
    save(OwnerSettings(mcp_source_access=True), home)
    quote = "Synthetic verified quote for encrypted local evidence context"
    with create_pool_for(seed["conninfo"], min_size=1, max_size=4) as pool:
        secrets = SecretStore()
        secrets.set("db_encryption_key", base64.b64encode(os.urandom(32)).decode())
        bind(pool, BodyKeyProvider(secrets))
        ctx = replace(local_app[1], pool=pool, home=home)
        source_id, evidence_id = uuid4(), uuid4()
        body = write_body(pool, seed["ws"], source_id, "P" * 600 + quote + "T" * 600)
        with pool.connection() as conn:
            connection = conn.execute(
                "insert into public.connection(workspace_id,provider,mode,account_label) "
                "values (%s,'email_upload','upload','encrypted MCP fixture') returning id",
                (seed["ws"],),
            ).fetchone()
            assert connection is not None
            conn.execute(
                "insert into public.source_record(id,workspace_id,connection_id,provider,"
                "external_id,"
                "client_id,kind,direction,policy_at_ingest,occurred_at,body_clean) "
                "values (%s,%s,%s,'email_upload',%s,%s,'email','in','full',now(),%s)",
                (
                    source_id,
                    seed["ws"],
                    connection["id"],
                    str(source_id),
                    seed["ids"]["full"],
                    body,
                ),
            )
            conn.execute(
                "insert into public.evidence(id,workspace_id,client_id,source_record_id,"
                "quote,locator) "
                "values (%s,%s,%s,%s,%s,'{}')",
                (evidence_id, seed["ws"], seed["ids"]["full"], source_id, quote),
            )
        app = FastAPI()
        app.include_router(http_router(build_server(engine_handler(ctx))))
        client = McpClient(TestClient(app, base_url="http://127.0.0.1:49152"), {})
        args = {"evidence_id": str(evidence_id), "with_context": True}
        result = rpc_result(client.tools_call("get_evidence", args))
        assert not result.get("isError"), result
        assert result["structuredContent"]["untrusted_context"] == "P" * 300 + quote + "T" * 300
        for key in ("excluded", "metadata"):
            hidden = seed["evidence"].get(key, uuid4())
            assert rpc_result(
                client.tools_call("get_evidence", {**args, "evidence_id": str(hidden)})
            )["isError"]
        secrets.delete("db_encryption_key")
        result = rpc_result(client.tools_call("get_evidence", args))
        assert result["isError"] and quote not in str(result)
