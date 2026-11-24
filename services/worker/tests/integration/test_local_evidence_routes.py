"""Bundled-Postgres twins of TS timeline-evidence and replay integration tests."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from firra.local.readctx import ReadContext
from tests.integration import test_local_routes as reference
from tests.unit.test_local_evidence_builders import NARRATIVE, TOTALS

AUTH = reference.AUTH
served = reference.served
pytestmark = pytest.mark.integration


def seed_client(conn: psycopg.Connection[DictRow], ws: UUID) -> UUID:
    row = conn.execute(
        """insert into public.client (workspace_id, name)
           values (%s, 'Synthetic client') returning id""",
        (ws,),
    ).fetchone()
    assert row
    return UUID(str(row["id"]))


def seed_evidence(conn: psycopg.Connection[DictRow], ws: UUID, cid: UUID, at: str) -> UUID:
    connection = conn.execute(
        """insert into public.connection (workspace_id, provider, mode, account_label)
        values (%s, 'email_upload', 'upload', 'Synthetic') returning id""",
        (ws,),
    ).fetchone()
    person = conn.execute(
        """insert into public.person (workspace_id, client_id, side, display_name, role_title)
        values (%s, %s, 'client', 'Synthetic stakeholder', 'CEO') returning id""",
        (ws, cid),
    ).fetchone()
    assert connection and person
    record = conn.execute(
        """insert into public.source_record
        (workspace_id, connection_id, provider, external_id, client_id, kind, direction,
         policy_at_ingest, occurred_at, author_person_id, body_clean, metadata)
        values (%s, %s, 'email_upload', %s, %s, 'email', 'in', 'full', %s, %s,
                'Synthetic verified evidence quote', %s) returning id""",
        (
            ws,
            connection["id"],
            str(uuid4()),
            cid,
            at,
            person["id"],
            Jsonb(dict(subject="  " + "S" * 140 + "  ")),
        ),
    ).fetchone()
    assert record
    evidence = conn.execute(
        """insert into public.evidence (workspace_id, client_id,
source_record_id, quote, locator, deep_link) values (%s, %s, %s,
'Synthetic verified evidence quote' , '{"start":0,"end":33}' ,
'https://example.test/synthetic' ) returning id""",
        (ws, cid, record["id"]),
    ).fetchone()
    assert evidence
    return UUID(str(evidence["id"]))


def seed_assessment(
    conn: psycopg.Connection[DictRow],
    ws: UUID,
    cid: UUID,
    day: str,
    *,
    previous: str | None = "stable",
) -> None:
    conn.execute(
        """insert into public.account_assessment (workspace_id, client_id,
as_of, run_kind, assessment_version, rule_version, risk_state,
previous_risk_state, state_since, expansion_state, strength)
values (%s, %s, %s, 'live' , 'test' , 'test' , 'watch' , %s, %s,
'none' , 'moderate' )""",
        (ws, cid, day, previous, day),
    )


def test_timeline_evidence_cursor_isolation_policy_and_audits(
    served: tuple[TestClient, ReadContext],
) -> None:
    http, ctx = served
    ws = ctx.identity.workspace_id
    with ctx.pool.connection() as conn:
        cid = seed_client(conn, ws)
        excluded = seed_client(conn, ws)
        foreign_ws = conn.execute(
            "insert into public.workspace (name) values ('Synthetic foreign') returning id"
        ).fetchone()
        assert foreign_ws
        foreign = seed_client(conn, foreign_ws["id"])
        foreign_evidence = seed_evidence(conn, foreign_ws["id"], foreign, "2025-01-01T00:00:00Z")
        ids = [
            seed_evidence(conn, ws, cid, at)
            for at in ("2025-01-02T00:00:00Z", "2025-01-02T00:00:00Z", "2025-01-01T00:00:00Z")
        ]
        hidden = seed_evidence(conn, ws, excluded, "2025-01-01T00:00:00Z")
        commitment = conn.execute(
            """insert into public.commitment (workspace_id, client_id,
owner_side, text, dedupe_key, stated_at, due_date, status,
closed_at) values (%s, %s, 'firm' , 'Synthetic commitment' ,
'test' , '2025-01-01T00:00:00Z' , '2025-01-02' , 'done' ,
'2025-01-03T00:00:00Z' ) returning id""",
            (ws, cid),
        ).fetchone()
        assert commitment
        conn.execute(
            """insert into public.commitment_evidence (workspace_id, client_id,
commitment_id, evidence_id) values (%s, %s, %s, %s)""",
            (ws, cid, commitment["id"], ids[0]),
        )
        seed_assessment(conn, ws, cid, "2025-01-04")
        conn.execute(
            """insert into public.outcome (workspace_id, client_id, event,
occurred_at, note) values (%s, %s, 'renewed' , '2025-01-05' ,
'PRIVATE BODY' )""",
            (ws, cid),
        )
        signal = conn.execute(
            """insert into public.signal (workspace_id, client_id, type,
severity, basis, inputs, occurred_at, dedupe_key, rule_version)
values (%s, %s, 'reduced_engagement' , 'medium' , 'metadata' ,
'{"messages_30":1}' , '2025-01-06T00:00:00Z' , 'volume' , 'test'
) returning id""",
            (ws, cid),
        ).fetchone()
        assert signal
        conn.execute(
            """insert into public.signal_event (workspace_id, client_id,
signal_id, change, severity, as_of, rule_version, inputs) values
(%s, %s, %s, 'opened' , 'medium' , '2025-01-06T00:00:00Z' ,
'test' , %s)""",
            (ws, cid, signal["id"], Jsonb(dict(quote="PRIVATE BODY"))),
        )
    with ctx.caller() as conn:
        conn.execute("select public.set_client_policy(%s, 'excluded')", (excluded,))
    base = f"/v1/workspaces/{ws}"
    successes = 0

    def read(path: str) -> dict[str, Any]:
        nonlocal successes
        response = http.get(base + path, headers=AUTH)
        assert response.status_code == 200
        successes += 1
        return dict(response.json())

    timeline = read(f"/clients/{cid}/timeline")
    assert [i["kind"] for i in timeline["items"]] == [
        "signal",
        "outcome",
        "state_change",
        "commitment",
        "commitment",
        "commitment",
    ]
    assert "PRIVATE BODY" not in str(timeline)
    first = read(f"/clients/{cid}/evidence?limit=1")
    assert first["next_cursor"]
    with ctx.pool.connection() as conn:
        seed_evidence(conn, ws, cid, "2025-01-07T00:00:00Z")
    second = read(f"/clients/{cid}/evidence?limit=100&cursor={first['next_cursor']}")
    assert {i["id"] for i in first["items"] + second["items"]} == {str(i) for i in ids}
    assert second["next_cursor"] is None
    single = read(f"/evidence/{ids[0]}")
    assert (
        len(single["subject"]) == 120
        and single["author_name"] == "Synthetic stakeholder"
        and single["author_role"] == "CEO"
    )
    assert single["deep_link"] == "https://example.test/synthetic" and "body_clean" not in single
    timeline_first = read(f"/clients/{cid}/timeline?limit=1")
    with ctx.pool.connection() as conn:
        seed_assessment(conn, ws, cid, "2025-01-08")
    rest = read(f"/clients/{cid}/timeline?cursor={timeline_first['next_cursor']}")
    assert [i["id"] for i in timeline_first["items"] + rest["items"]] == [
        i["id"] for i in timeline["items"]
    ]
    for client_id in (foreign, excluded, uuid4()):
        for suffix in ("timeline", "evidence"):
            assert http.get(f"{base}/clients/{client_id}/{suffix}", headers=AUTH).status_code == 404
    for eid in (hidden, foreign_evidence, uuid4()):
        assert http.get(f"{base}/evidence/{eid}", headers=AUTH).status_code == 404
    for query in ("cursor=bad", "limit=0", "limit=101", "limit=x"):
        assert http.get(f"{base}/clients/{cid}/timeline?{query}", headers=AUTH).status_code == 422
    assert (
        http.get(
            f"/v1/workspaces/{foreign_ws['id']}/clients/{cid}/timeline", headers=AUTH
        ).status_code
        == 403
    )
    assert http.get(f"{base}/clients/{cid}/timeline").status_code == 401
    with ctx.caller() as conn:
        conn.execute("select public.set_client_policy(%s, 'metadata_only')", (cid,))
    with ctx.pool.connection() as conn:
        for table in ("evidence", "commitment", "account_assessment"):
            # Fixture-only fixed table identifiers; never used in production SQL.
            rows = conn.execute(
                psycopg.sql.SQL(
                    "select id from public.{} where workspace_id = %s and client_id = %s"
                ).format(psycopg.sql.Identifier(table)),
                (ws, cid),
            ).fetchall()
            assert rows == []
        seed_assessment(conn, ws, cid, "2025-01-09")
    metadata = read(f"/clients/{cid}/timeline")
    assert [i["kind"] for i in metadata["items"]] == ["state_change", "signal", "outcome"]
    assert metadata["items"][1]["evidence_ids"] == [] and "PRIVATE BODY" not in str(metadata)
    assert read(f"/clients/{cid}/evidence") == dict(items=[], next_cursor=None)
    assert http.get(f"{base}/evidence/{ids[0]}", headers=AUTH).status_code == 404
    with ctx.pool.connection() as conn:
        audits = conn.execute(
            """select action, target_type, target_id, metadata, actor_id from
public.audit_log where workspace_id = %s and action in (
'read.client_timeline' , 'read.client_evidence' , 'read.evidence'
)""",
            (ws,),
        ).fetchall()
    assert len(audits) == successes
    for audit in audits:
        assert audit["actor_id"] == ctx.identity.user_id and list(audit["metadata"]) == ["count"]
        assert audit["target_type"] == (
            "evidence" if audit["action"] == "read.evidence" else "client"
        )


def test_replay_runs_detail_isolation_and_policy_deletion(
    served: tuple[TestClient, ReadContext],
) -> None:
    http, ctx = served
    ws = ctx.identity.workspace_id
    with ctx.pool.connection() as conn:
        cid = seed_client(conn, ws)
        foreign_ws = conn.execute(
            "insert into public.workspace (name) values ('Synthetic foreign') returning id"
        ).fetchone()
        assert foreign_ws
        foreign_client = seed_client(conn, foreign_ws["id"])
        run_ids = []
        for workspace in (ws, ws, foreign_ws["id"]):
            row = conn.execute(
                """insert into public.replay_run (workspace_id, window_start,
window_end, status, finished_at, summary) values (%s,
'2026-09-01' , '2026-09-30' , 'succeeded' ,
'2026-09-30T12:00:00Z' , %s) returning id""",
                (workspace, Jsonb(dict(totals=TOTALS))),
            ).fetchone()
            assert row
            run_ids.append(row["id"])
        run, legacy, foreign_run = run_ids
        for workspace, replay, client in (
            (ws, run, cid),
            (foreign_ws["id"], foreign_run, foreign_client),
        ):
            conn.execute(
                """insert into public.replay_narrative (workspace_id, replay_run_id,
client_id, version, narrative) values (%s, %s, %s, 1, %s)""",
                (workspace, replay, client, Jsonb(NARRATIVE)),
            )
    base = f"/v1/workspaces/{ws}/replay/runs"
    detail = f"/{run}/clients/{cid}"
    for _ in range(2):
        listed = http.get(base, headers=AUTH)
        assert listed.status_code == 200
        items = {i["id"]: i for i in listed.json()["items"]}
        assert items[str(run)]["narrated"] is True and items[str(legacy)]["narrated"] is False
        assert str(foreign_run) not in items
        response = http.get(base + detail, headers=AUTH)
        assert response.status_code == 200 and response.json()["narrative"] == NARRATIVE
    for suffix in (
        f"/{legacy}/clients/{cid}",
        f"/{foreign_run}/clients/{cid}",
        f"/{run}/clients/{foreign_client}",
        f"/{uuid4()}/clients/{cid}",
    ):
        assert http.get(base + suffix, headers=AUTH).status_code == 404
    with ctx.pool.connection() as conn:
        audits = conn.execute(
            """select action, target_id from public.audit_log where workspace_id
= %s and action in ( 'read.replay_runs' , 'read.replay_client' )""",
            (ws,),
        ).fetchall()
    assert len(audits) == 4 and all(
        a["target_id"] == cid for a in audits if a["action"] == "read.replay_client"
    )
    with ctx.caller() as conn:
        conn.execute("select public.set_client_policy(%s, 'metadata_only')", (cid,))
        assert (
            conn.execute(
                "select id from public.replay_narrative where workspace_id = %s", (ws,)
            ).fetchall()
            == []
        )
    assert http.get(base + detail, headers=AUTH).status_code == 404
    metadata = {**NARRATIVE, "visible_at_detection": []}
    with ctx.pool.connection() as conn:
        conn.execute(
            """insert into public.replay_narrative (workspace_id, replay_run_id,
client_id, version, narrative) values (%s, %s, %s, 1, %s)""",
            (ws, run, cid, Jsonb(metadata)),
        )
    response = http.get(base + detail, headers=AUTH)
    assert response.status_code == 200 and response.json()["evidence_ids"] == []
    with ctx.caller() as conn:
        conn.execute("select public.set_client_policy(%s, 'excluded')", (cid,))
        assert (
            conn.execute(
                "select id from public.replay_narrative where workspace_id = %s", (ws,)
            ).fetchall()
            == []
        )
        assert (
            conn.execute(
                "select id from public.replay_narrative where workspace_id = %s",
                (foreign_ws["id"],),
            ).fetchall()
            == []
        )
    assert http.get(base + detail, headers=AUTH).status_code == 404
    with ctx.pool.connection() as conn:
        audits = conn.execute(
            """select id from public.audit_log where workspace_id = %s and
action in ( 'read.replay_runs' , 'read.replay_client' )""",
            (ws,),
        ).fetchall()
    assert len(audits) == 5


def test_failed_audit_returns_no_evidence(
    served: tuple[TestClient, ReadContext], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, ctx = served
    with ctx.pool.connection() as conn:
        cid = seed_client(conn, ctx.identity.workspace_id)
        eid = seed_evidence(conn, ctx.identity.workspace_id, cid, "2025-01-01T00:00:00Z")

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic audit unavailable")

    monkeypatch.setattr("firra.local.routes.evidence.audit_read", fail)
    response = http.get(f"/v1/workspaces/{ctx.identity.workspace_id}/evidence/{eid}", headers=AUTH)
    assert response.status_code == 500 and "quote" not in response.json()


def test_timeline_skips_more_than_one_page_of_unchanged_states(
    served: tuple[TestClient, ReadContext],
) -> None:
    http, ctx = served
    ws = ctx.identity.workspace_id
    with ctx.pool.connection() as conn:
        cid = seed_client(conn, ws)
        seed_assessment(conn, ws, cid, "2025-01-01")
        for i in range(150):
            day = (datetime(2025, 1, 2, tzinfo=UTC) + timedelta(days=i)).date().isoformat()
            seed_assessment(conn, ws, cid, day, previous="watch")
    result = http.get(f"/v1/workspaces/{ws}/clients/{cid}/timeline?limit=1", headers=AUTH)
    assert result.status_code == 200
    assert len(result.json()["items"]) == 1 and result.json()["next_cursor"] is None
    assert result.json()["items"][0]["occurred_at"].startswith("2025-01-01")


def test_evidence_boundary_spans_sources_and_preserves_microseconds(
    served: tuple[TestClient, ReadContext],
) -> None:
    http, ctx = served
    ws = ctx.identity.workspace_id
    with ctx.pool.connection() as conn:
        cid = seed_client(conn, ws)
        ids = {str(seed_evidence(conn, ws, cid, "2025-01-01T00:00:00.000001Z")) for _ in range(105)}
        newest = seed_evidence(conn, ws, cid, "2025-01-01T02:00:00.000002+02:00")
    base = f"/v1/workspaces/{ws}/clients/{cid}/evidence"
    cursor = None
    seen: list[str] = []
    while True:
        path = base + "?limit=50" + (f"&cursor={cursor}" if cursor else "")
        response = http.get(path, headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        seen.extend(i["id"] for i in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert len(seen) <= 106
    assert seen[0] == str(newest)
    assert len(seen) == len(set(seen)) == 106 and set(seen) == ids | {str(newest)}


def test_replay_reads_all_weekly_rows(
    served: tuple[TestClient, ReadContext],
) -> None:
    http, ctx = served
    ws = ctx.identity.workspace_id
    with ctx.pool.connection() as conn:
        cid = seed_client(conn, ws)
        run = conn.execute(
            """insert into public.replay_run (workspace_id, window_start, window_end, status)
               values (%s, '2025-01-01', '2025-12-31', 'succeeded') returning id""",
            (ws,),
        ).fetchone()
        assert run
        conn.execute(
            """insert into public.replay_narrative
               (workspace_id, replay_run_id, client_id, version, narrative)
               values (%s, %s, %s, 1, %s)""",
            (ws, run["id"], cid, Jsonb(NARRATIVE)),
        )
        conn.execute(
            """insert into public.account_assessment
               (workspace_id, client_id, as_of, run_kind, replay_run_id, assessment_version,
                rule_version, risk_state, state_since, expansion_state, strength)
               select %s, %s, '2024-01-01'::date + i, 'replay', %s, 'test', 'test',
                      'high', '2024-01-01'::date + i, 'none', 'strong'
                 from generate_series(0, 509) i""",
            (ws, cid, run["id"]),
        )
    response = http.get(f"/v1/workspaces/{ws}/replay/runs/{run['id']}/clients/{cid}", headers=AUTH)
    assert response.status_code == 200
    assert len(response.json()["weeks"]) == 510
    assert all(w["risk"] == "high" for w in response.json()["weeks"])
    assert response.json()["evidence_ids"] == NARRATIVE["visible_at_detection"][0]["evidence_ids"]
