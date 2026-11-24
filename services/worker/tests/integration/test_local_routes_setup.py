"""Twin of the local-relevant half of apps/api/test/integration/setup.test.ts."""

from typing import Any
from uuid import uuid4

import psycopg
import pytest

from tests.integration.conftest import LOCAL_AUTH

pytestmark = pytest.mark.integration


def test_setup_writes_reads_and_audit(local_app: tuple[Any, Any, str]) -> None:
    client, ctx, conninfo = local_app
    ws = ctx.identity.workspace_id
    base = f"/v1/workspaces/{ws}"

    def call(method: str, path: str, body: Any = None) -> Any:
        return client.request(method, f"{base}{path}", json=body, headers=LOCAL_AUTH)

    created = call("POST", "/clients", {"name": "Synthetic client", "domains": ["fixture-a.test"]})
    assert created.status_code == 200, created.text
    cid = created.json()["id"]
    assert (
        call("PATCH", f"/clients/{cid}", {"name": "Renamed", "status": "churned"}).status_code
        == 204
    )
    assert (
        call("PUT", f"/clients/{cid}/domains", {"domains": ["synthetic.test"]}).status_code == 204
    )
    assert call("GET", f"/clients/{cid}/domains").json() == {"domains": ["synthetic.test"]}
    # excluded must be confirmed, then hides the client from intelligence reads but not from setup
    assert call("PUT", f"/clients/{cid}/policy", {"policy": "excluded"}).status_code == 422
    assert call("POST", "/clients", {"name": "X", "policy": "excluded"}).status_code == 422
    ok = call("PUT", f"/clients/{cid}/policy", {"policy": "excluded", "confirm_exclusion": True})
    assert ok.status_code == 204
    assert call("GET", f"/clients/{cid}").status_code == 404
    listed = call("GET", "/setup/clients").json()["items"]
    assert [(i["id"], i["policy"]) for i in listed if i["id"] == cid] == [(cid, "excluded")]
    assert call("PUT", f"/clients/{cid}/policy", {"policy": "metadata_only"}).status_code == 204
    assert call("PUT", f"/clients/{cid}/policy", {"policy": "bogus"}).status_code == 422
    assert call("PATCH", f"/clients/{cid}", {"name": "", "status": "active"}).status_code == 422
    assert (
        call("PATCH", f"/clients/{uuid4()}", {"name": "N", "status": "active"}).status_code == 404
    )

    svc = call("POST", "/services", {"name": "Synthetic service", "keywords": [" CRM ", "crm"]})
    assert svc.status_code == 200, svc.text
    sid = svc.json()["id"]
    saved = call(
        "PUT", f"/services/{sid}", {"name": "Updated", "description": "d", "keywords": ["strategy"]}
    )
    assert saved.status_code == 200 and saved.json()["id"] == sid
    items = call("GET", "/services").json()["items"]
    assert [(i["name"], i["keywords"]) for i in items] == [("Updated", ["strategy"])]
    assert call("PUT", f"/services/{uuid4()}", {"name": "N"}).status_code == 404
    assert call("POST", "/services", {"name": "N", "keywords": ["x"] * 21}).status_code == 422
    assert call("DELETE", f"/services/{sid}").status_code == 204
    assert call("DELETE", f"/services/{sid}").status_code == 404

    foreign_ws, conn_own, conn_foreign = uuid4(), uuid4(), uuid4()
    with psycopg.connect(conninfo, autocommit=True) as db:
        db.execute("insert into public.workspace (id, name) values (%s, 'Foreign')", (foreign_ws,))
        for cid_, wid in ((conn_own, ws), (conn_foreign, foreign_ws)):
            db.execute(
                "insert into public.connection (id, workspace_id, provider, mode, account_label,"
                " token_ciphertext) values (%s,%s,'email_upload','upload','synthetic','tok')",
                (cid_, wid),
            )
    assert call("DELETE", f"/connections/{conn_foreign}").status_code == 404
    assert call("DELETE", f"/connections/{conn_own}").status_code == 204
    with psycopg.connect(conninfo) as db:
        rows = dict(
            (str(r[0]), (r[1], r[2]))
            for r in db.execute(
                "select id, status::text, token_ciphertext from public.connection"
                " where id in (%s, %s)",
                (conn_own, conn_foreign),
            )
        )
    assert rows[str(conn_own)] == ("revoked", None)
    assert rows[str(conn_foreign)] == ("active", "tok")

    replay = call(
        "POST", "/replay/runs", {"window_start": "2026-01-01", "window_end": "2026-02-01"}
    )
    assert replay.status_code == 200, replay.text
    bad = {"window_start": "2026-02-01", "window_end": "2026-01-01"}
    assert call("POST", "/replay/runs", bad).status_code == 422
    assert (
        call(
            "POST",
            "/replay/runs",
            {**bad, "window_start": "2024-01-01", "window_end": "2026-01-01"},
        ).status_code
        == 422
    )

    with psycopg.connect(conninfo) as db:
        actions = [
            r[0]
            for r in db.execute("select action from public.audit_log where workspace_id=%s", (ws,))
        ]
    for action in (
        "client.created",
        "client.updated",
        "client.domains_set",
        "client.policy_changed",
        "service.created",
        "service.updated",
        "service.deleted",
        "connection.disconnected",
        "replay_run.requested",
        "read.setup_services",
        "read.setup_domains",
    ):
        assert action in actions, action
