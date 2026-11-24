"""GET /clients and /changes on the local app."""

from typing import Any
from uuid import uuid4

import psycopg
import pytest

from tests.integration.conftest import LOCAL_AUTH

pytestmark = pytest.mark.integration


def test_clients_list_filters_status_and_hides_excluded(local_app: tuple[Any, Any, str]) -> None:
    client, ctx, conninfo = local_app
    ws = ctx.identity.workspace_id
    base = f"/v1/workspaces/{ws}"
    active, churned, excluded, meta = (uuid4() for _ in range(4))
    with psycopg.connect(conninfo, autocommit=True) as db:
        db.execute(
            "insert into public.client (id, workspace_id, name, status, policy) values "
            "(%s,%s,'Acme','active','full'),(%s,%s,'Bravo','churned','full'),"
            "(%s,%s,'Hidden','active','excluded'),(%s,%s,'Meta','active','metadata_only')",
            (active, ws, churned, ws, excluded, ws, meta, ws),
        )

    def names(status: str) -> list[str]:
        res = client.get(f"{base}/clients?status={status}", headers=LOCAL_AUTH)
        return [i["name"] for i in res.json()["items"]]

    assert names("active") == ["Acme", "Meta"]
    assert names("churned") == ["Bravo"]
    assert names("all") == ["Acme", "Bravo", "Meta"]
    default = client.get(f"{base}/clients", headers=LOCAL_AUTH)
    assert default.status_code == 200 and [i["name"] for i in default.json()["items"]] == [
        "Acme",
        "Meta",
    ]
    assert client.get(f"{base}/clients?status=bogus", headers=LOCAL_AUTH).status_code == 422


def test_changes_validates_since_and_answers(local_app: tuple[Any, Any, str]) -> None:
    client, ctx, _ = local_app
    base = f"/v1/workspaces/{ctx.identity.workspace_id}"
    ok = client.get(f"{base}/changes", headers=LOCAL_AUTH)
    assert ok.status_code == 200 and ok.json()["items"] == [] and "since" in ok.json()
    for bad in ("2000-01-01", "2999-01-01", "yesterday", "2026-13-40x"):
        assert client.get(f"{base}/changes?since={bad}", headers=LOCAL_AUTH).status_code == 422
