"""Twin of apps/api/test/integration/decisions.test.ts on the local app."""
# ruff: noqa: E501  (long SQL fixtures)

import json
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.integration.conftest import LOCAL_AUTH

pytestmark = pytest.mark.integration


def _insert_card(conn: psycopg.Connection[Any], ws: UUID, client: UUID, **extra: Any) -> UUID:
    card = uuid4()
    row = {
        "id": card,
        "workspace_id": ws,
        "client_id": client,
        "week": "2026-09-28",
        "kind": "risk",
        "title": "Synthetic title",
        "body": "Synthetic body",
        "locale": "en",
        "score": 1,
        "priority": 1,
        "inputs": json.dumps({}),
        "model": "fixture",
        "prompt_version": "fixture",
        "dedupe_key": f"fixture-{card}",
        "play": "fix_first",
        "facts": json.dumps([{"basis": "metadata"}]),
        "interpretation": "Synthetic interpretation",
        "recommendation": "Synthetic recommendation",
        "strength": "strong",
        **extra,
    }
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    conn.execute(f"insert into public.decision_card ({cols}) values ({marks})", list(row.values()))  # noqa: S608
    return card


def test_decisions_feedback_outcomes_and_audit(local_app: tuple[Any, Any, str]) -> None:
    client, ctx, conninfo = local_app
    ws = ctx.identity.workspace_id
    base = f"/v1/workspaces/{ws}"
    full, excluded, metadata, foreign_client = (uuid4() for _ in range(4))
    foreign_ws = uuid4()
    with psycopg.connect(conninfo, autocommit=True) as db:
        db.execute("insert into public.workspace (id, name) values (%s, 'Foreign')", (foreign_ws,))
        db.execute(
            "insert into public.client (id, workspace_id, name, policy) values "
            "(%s,%s,'Synthetic full','full'),(%s,%s,'Synthetic excluded','excluded'),"
            "(%s,%s,'Synthetic metadata','metadata_only'),(%s,%s,'Synthetic foreign','full')",
            (full, ws, excluded, ws, metadata, ws, foreign_client, foreign_ws),
        )
        card_full = _insert_card(db, ws, full)
        card_excluded = _insert_card(db, ws, excluded)
        card_metadata = _insert_card(db, ws, metadata)
        card_foreign = _insert_card(db, foreign_ws, foreign_client)
        conn_id = db.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label) "
            "values (%s, 'email_upload', 'upload', 'Synthetic') returning id",
            (ws,),
        ).fetchone()
        assert conn_id is not None
        record = db.execute(
            "insert into public.source_record (workspace_id, client_id, connection_id, provider, kind,"
            " direction, policy_at_ingest, external_id, occurred_at, body_clean) values "
            "(%s,%s,%s,'email_upload','email','in','full',%s,'2026-09-28T00:00:00Z','Synthetic verified quote')"
            " returning id",
            (ws, full, conn_id[0], str(uuid4())),
        ).fetchone()
        assert record is not None
        evidence = db.execute(
            "insert into public.evidence (workspace_id, client_id, source_record_id, quote, locator) "
            "values (%s,%s,%s,'Synthetic verified quote','{\"start\":0,\"end\":24}') returning id",
            (ws, full, record[0]),
        ).fetchone()
        assert evidence is not None
        db.execute(
            "insert into public.decision_evidence (workspace_id, client_id, decision_card_id, evidence_id)"
            " values (%s,%s,%s,%s)",
            (ws, full, card_full, evidence[0]),
        )

    def listing(query: str = "") -> dict[str, Any]:
        res = client.get(f"{base}/decisions?status=all{query}", headers=LOCAL_AUTH)
        assert res.status_code == 200, res.text
        return res.json()  # type: ignore[no-any-return]

    ids = {i["id"] for i in listing()["items"]}
    assert ids == {str(card_full), str(card_metadata)}  # excluded and foreign are invisible (RLS)
    metadata_item = next(i for i in listing()["items"] if i["client_id"] == str(metadata))
    assert metadata_item["evidence_ids"] == []

    # feedback: the second call updates the same row; the card closes as done
    feedback_url = f"{base}/decisions/{card_full}/feedback"
    first = client.post(
        feedback_url, json={"verdict": "wrong", "note": "Synthetic note"}, headers=LOCAL_AUTH
    )
    second = client.post(
        feedback_url, json={"verdict": "useful", "action": "taken"}, headers=LOCAL_AUTH
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    latest = next(i for i in listing()["items"] if i["id"] == str(card_full))
    assert latest["evidence_ids"] == [str(evidence[0])]
    assert latest["status"] == "done"
    assert latest["feedback"] == {"verdict": "useful", "action": "taken", "note": None}

    for hidden in (card_excluded, card_foreign, uuid4()):
        res = client.post(
            f"{base}/decisions/{hidden}/feedback", json={"verdict": "useful"}, headers=LOCAL_AUTH
        )
        assert res.status_code == 404 and res.json()["error"] == "not_found"
    assert (
        client.post(feedback_url, json={"verdict": "nope"}, headers=LOCAL_AUTH).status_code == 422
    )

    # outcomes
    body = {"event": "churned", "occurred_at": "2026-10-01"}
    for hidden_client in (foreign_client, excluded):
        res = client.post(f"{base}/clients/{hidden_client}/outcomes", json=body, headers=LOCAL_AUTH)
        assert res.status_code == 404
    bad = client.post(
        f"{base}/clients/{full}/outcomes", json={**body, "amount_change": 5}, headers=LOCAL_AUTH
    )
    assert bad.status_code == 422  # amount without currency
    late = client.post(
        f"{base}/clients/{full}/outcomes",
        json={**body, "notice_at": "2026-10-02"},
        headers=LOCAL_AUTH,
    )
    assert late.status_code == 422  # notice after the outcome
    created = client.post(
        f"{base}/clients/{full}/outcomes",
        json={**body, "notice_at": "2026-09-01"},
        headers=LOCAL_AUTH,
    )
    assert created.status_code == 200
    outcome_id = created.json()["id"]
    with psycopg.connect(conninfo) as db:
        row = db.execute(
            "select status::text, churned_at::text from public.client where id = %s", (full,)
        ).fetchone()
        assert row == ("churned", "2026-10-01")
    wrong_workspace = client.delete(
        f"/v1/workspaces/{foreign_ws}/outcomes/{outcome_id}", headers=LOCAL_AUTH
    )
    assert wrong_workspace.status_code == 403
    assert client.delete(f"{base}/outcomes/{outcome_id}", headers=LOCAL_AUTH).status_code == 204
    with psycopg.connect(conninfo) as db:
        row = db.execute(
            "select status::text, churned_at from public.client where id = %s", (full,)
        ).fetchone()
        assert row == ("active", None)

    # routine cards: importance and kind filters run as the owner through RLS
    with psycopg.connect(conninfo, autocommit=True) as db:
        r_full = _insert_card(
            db,
            ws,
            full,
            importance="important",
            kind="state_change",
            week="2026-10-05",
            surfaced_on="2026-10-06",
            reason_code="risk_rose_elevated",
            model="deterministic",
            prompt_version="facts.v1",
        )
        r_excluded = _insert_card(
            db,
            ws,
            excluded,
            importance="important",
            kind="state_change",
            week="2026-10-05",
            reason_code="risk_rose_elevated",
        )
        r_meta = _insert_card(
            db,
            ws,
            metadata,
            importance="fyi",
            kind="expansion",
            week="2026-10-05",
            reason_code="risk_rose_elevated",
        )
    important = listing("&importance=important")
    assert [i["id"] for i in important["items"]] == [str(r_full)]
    assert (
        important["items"][0]["importance"] == "important"
        and important["items"][0]["surfaced_on"] == "2026-10-06"
    )
    assert [i["id"] for i in listing("&kind=expansion")["items"]] == [str(r_meta)]
    assert listing("&importance=fyi&kind=state_change")["items"] == []
    assert str(r_excluded) not in {i["id"] for i in listing()["items"]}
    assert client.get(f"{base}/decisions?status=bogus", headers=LOCAL_AUTH).status_code == 422
    assert client.get(f"{base}/decisions?limit=0", headers=LOCAL_AUTH).status_code == 422
    assert client.get(f"{base}/decisions?cursor=%25", headers=LOCAL_AUTH).status_code == 422

    # paging: limit 1 walks every visible card exactly once
    seen: list[str] = []
    cursor = ""
    for _ in range(10):
        page = listing(f"&limit=1{cursor}")
        seen += [i["id"] for i in page["items"]]
        if page["next_cursor"] is None:
            break
        cursor = f"&cursor={page['next_cursor']}"
    assert sorted(seen) == sorted({i["id"] for i in listing()["items"]}) and len(seen) == len(
        set(seen)
    )

    with psycopg.connect(conninfo) as db:
        actions = [
            r[0]
            for r in db.execute(
                "select action from public.audit_log where workspace_id = %s", (ws,)
            )
        ]
    assert actions.count("decision_feedback.recorded") == 2
    assert actions.count("outcome.recorded") == 1 and actions.count("outcome.deleted") == 1
    assert actions.count("read.decisions") >= 8
