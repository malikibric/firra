"""Reference routes (me, routines) on the bundled database: RLS as the owner, audit, errors."""

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from firra.db.pool import create_pool_for
from firra.local.bootstrap import ensure_local_owner
from firra.local.pg import PostgresSupervisor
from firra.local.readctx import ReadContext
from firra.local.routes import all_routers
from firra.local.server import create_local_app
from firra_contracts import EngineStatus

pytestmark = pytest.mark.integration
PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")
TOKEN = "t" * 40
PORT = 49152
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def served() -> Iterator[tuple[TestClient, ReadContext]]:
    if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    root = Path(tempfile.mkdtemp(prefix="firra-routes-", dir="/tmp"))
    pg = PostgresSupervisor(Path(PG_BIN), root / "data", root / "run")
    try:
        conninfo = pg.ensure_ready()
        identity = ensure_local_owner(conninfo, timezone="UTC")
        with create_pool_for(conninfo, min_size=1, max_size=3) as pool:
            ctx = ReadContext(pool=pool, identity=identity)
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
                api_token=TOKEN, port=PORT, status=lambda: status, routers=all_routers(ctx)
            )
            yield TestClient(app, base_url=f"http://127.0.0.1:{PORT}"), ctx
    finally:
        pg.stop()
        shutil.rmtree(root, ignore_errors=True)


def test_me_lists_the_owner_and_the_one_workspace(served: tuple[TestClient, ReadContext]) -> None:
    client, ctx = served
    body = client.get("/v1/me", headers=AUTH).json()
    assert body["user"] == {"id": str(ctx.identity.user_id), "email": "owner@firra.local"}
    assert body["memberships"] == [
        {
            "workspace_id": str(ctx.identity.workspace_id),
            "workspace_name": "My firm",
            "role": "owner",
        }
    ]
    assert body["contributions"] == []


def test_routines_default_then_switch_then_audit(served: tuple[TestClient, ReadContext]) -> None:
    client, ctx = served
    base = f"/v1/workspaces/{ctx.identity.workspace_id}"
    first = client.get(f"{base}/routines", headers=AUTH)
    assert first.status_code == 200
    items = first.json()["items"]
    assert [i["kind"] for i in items] == ["morning_scan", "commitment_watch", "pre_meeting_brief"]
    assert all(
        i["enabled"] is True and i["created"] is False and i["last_run"] is None for i in items
    )

    put = client.put(f"{base}/routines/morning_scan", json={"enabled": False}, headers=AUTH)
    assert put.status_code == 200 and "id" in put.json()
    after = {i["kind"]: i for i in client.get(f"{base}/routines", headers=AUTH).json()["items"]}
    assert after["morning_scan"]["enabled"] is False and after["morning_scan"]["created"] is True

    with ctx.pool.connection() as conn:
        actions = [
            r["action"]
            for r in conn.execute(
                "select action from public.audit_log where workspace_id = %s order by created_at",
                (ctx.identity.workspace_id,),
            ).fetchall()
        ]
    assert actions.count("read.routines") == 2
    assert "routine.switched" in actions or any(a.startswith("routine") for a in actions)


def test_errors_use_the_v1_model(served: tuple[TestClient, ReadContext]) -> None:
    client, ctx = served
    base = f"/v1/workspaces/{ctx.identity.workspace_id}"
    foreign = client.get(f"/v1/workspaces/{uuid4()}/routines", headers=AUTH)
    assert foreign.status_code == 403 and foreign.json()["error"] == "forbidden"
    bad_id = client.get("/v1/workspaces/not-a-uuid/routines", headers=AUTH)
    assert bad_id.status_code == 422 and bad_id.json()["error"] == "invalid_request"
    bad_kind = client.put(f"{base}/routines/nope", json={"enabled": True}, headers=AUTH)
    assert bad_kind.status_code == 422
    bad_body = client.put(
        f"{base}/routines/morning_scan", json={"enabled": "yes", "x": 1}, headers=AUTH
    )
    assert bad_body.status_code == 422 and bad_body.json()["error"] == "invalid_request"
    assert set(bad_body.json()) == {"error", "message", "request_id"}
