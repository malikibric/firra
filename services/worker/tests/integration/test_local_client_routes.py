"""TS read/changes/clients integration twins on the bundled Postgres, under owner RLS."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from firra.db import local_clients as repository
from firra.db.types import WorkspaceId
from firra.local.readctx import ReadContext
from firra.local.routes.client_builders import build_changes, build_client_list_item
from tests.integration import test_local_routes as reference
from tests.unit.test_local_client_builders import ASSESSMENT, PARTS

AUTH = reference.AUTH
served = reference.served
pytestmark = pytest.mark.integration


def seed_client(
    conn: psycopg.Connection[DictRow],
    ws: UUID,
    name: str,
    *,
    policy: str = "full",
    status: str = "active",
) -> UUID:
    row = conn.execute(
        """insert into public.client (workspace_id, name, policy, status) values (%s,
%s, %s, %s) returning id""",
        (ws, name, policy, status),
    ).fetchone()
    assert row
    return row["id"]  # type: ignore[no-any-return]


def assessment(
    conn: psycopg.Connection[DictRow],
    ws: UUID,
    cid: UUID,
    *,
    day: str | None = None,
    risk: str = "high",
    previous: str | None = "watch",
    drivers: list[dict[str, Any]] | None = None,
) -> None:
    day = day or datetime.now(UTC).date().isoformat()
    conn.execute(
        """insert into public.account_assessment
(workspace_id, client_id, as_of, run_kind, assessment_version,
rule_version,
risk_state, previous_risk_state, state_since, expansion_state, strength,
play, drivers, caveats)
values (%s, %s, %s, 'live', 'test', 'test', %s, %s, %s, 'none', 'moderate',
'monitor', %s, %s)""",
        (
            ws,
            cid,
            day,
            risk,
            previous,
            day,
            Jsonb(drivers if drivers is not None else ASSESSMENT["drivers"]),
            Jsonb(ASSESSMENT["caveats"]),
        ),
    )


def source(conn: psycopg.Connection[DictRow], ws: UUID, cid: UUID) -> tuple[UUID, UUID]:
    connection = conn.execute(
        """insert into public.connection
(workspace_id, provider, mode, account_label) values (%s, 'email_upload',
'upload', 'Synthetic') returning id""",
        (ws,),
    ).fetchone()
    assert connection
    record = conn.execute(
        """insert into public.source_record
(workspace_id, connection_id, provider, external_id, kind, client_id,
policy_at_ingest, occurred_at, direction, body_clean)
values (%s, %s, 'email_upload', %s, 'email', %s, 'full', now(), 'in', 'Synthetic
verified quote') returning id""",
        (ws, connection["id"], str(uuid4()), cid),
    ).fetchone()
    assert record
    evidence = conn.execute(
        """insert into public.evidence
(workspace_id, source_record_id, client_id, quote, locator)
values (%s, %s, %s, 'Synthetic verified quote', '{"start": 0, "end": 24}')
returning id""",
        (ws, record["id"], cid),
    ).fetchone()
    assert evidence
    return record["id"], evidence["id"]


def test_attention_rls_cache_and_audit(served: tuple[TestClient, ReadContext]) -> None:
    http, ctx = served
    ws = ctx.identity.workspace_id
    with ctx.pool.connection() as conn:
        for name, policy, risk in [
            ("Visible Full", "full", "high"),
            ("Visible Metadata", "metadata_only", "elevated"),
            ("Hidden Excluded", "excluded", "high"),
            ("Calm", "full", "stable"),
        ]:
            cid = seed_client(conn, ws, name, policy=policy)
            assessment(conn, ws, cid, risk=risk, previous=None, drivers=[])
        foreign = conn.execute(
            "insert into public.workspace (name) values ('Synthetic foreign') returning id"
        ).fetchone()
        assert foreign
        assessment(conn, foreign["id"], seed_client(conn, foreign["id"], "Other Workspace"))
    path = f"/v1/workspaces/{ws}/attention"
    response = http.get(path, headers=AUTH)
    assert response.status_code == 200
    assert [i["name"] for i in response.json()["items"]] == ["Visible Full", "Visible Metadata"]
    assert "Hidden Excluded" not in response.text and "Other Workspace" not in response.text
    assert response.headers["cache-control"] == "no-store"
    UUID(response.headers["x-request-id"])
    assert http.get(path).status_code == 401
    assert http.get(f"/v1/workspaces/{uuid4()}/attention", headers=AUTH).status_code == 403
    with ctx.pool.connection() as conn:
        rows = conn.execute(
            """select actor_id, action, metadata from public.audit_log where workspace_id
= %s and action = 'read.attention'""",
            (ws,),
        ).fetchall()
    assert rows == [
        dict(actor_id=ctx.identity.user_id, action="read.attention", metadata=dict(count=2))
    ]


def test_client_detail_claims_latest_card_history_policy_and_old_assessment(
    served: tuple[TestClient, ReadContext],
) -> None:
    http, ctx = served
    ws = ctx.identity.workspace_id
    with ctx.pool.connection() as conn:
        full = seed_client(conn, ws, "A Full")
        meta = seed_client(conn, ws, "B Metadata")
        excluded = seed_client(conn, ws, "C Excluded", policy="excluded")
        churned = seed_client(conn, ws, "D Churned", status="churned")
        prospect = seed_client(conn, ws, "E Prospect", status="prospect")
        for cid in (full, meta):
            assessment(conn, ws, cid, day="2025-01-01", risk="watch", previous="stable")
            assessment(conn, ws, cid, day="2025-01-02", risk="watch", previous="watch")
            conn.execute(
                """insert into public.person (workspace_id, client_id, side, display_name,
role_title, is_decision_maker, is_champion) values (%s, %s, 'client',
'Synthetic Person', 'Director', true, true)""",
                (ws, cid),
            )
            _, eid = source(conn, ws, cid)
            commitment = conn.execute(
                """insert into public.commitment (workspace_id, client_id, owner_side, text,
stated_at, dedupe_key)
values (%s, %s, 'firm', 'Synthetic commitment', now(), 'test') returning id""",
                (ws, cid),
            ).fetchone()
            goal = conn.execute(
                """insert into public.client_goal (workspace_id, client_id, text, stated_at,
dedupe_key)
values (%s, %s, 'Synthetic goal', now(), 'test') returning id""",
                (ws, cid),
            ).fetchone()
            need = conn.execute(
                """insert into public.latent_need (workspace_id, client_id, topic,
mention_count, thread_count, first_seen_at, last_seen_at, cluster_key,
rule_version)
values (%s, %s, 'Synthetic need', 3, 2, now(), now(), 'test', 'test')
returning id""",
                (ws, cid),
            ).fetchone()
            assert commitment and goal and need
            conn.execute(
                """insert into public.commitment_evidence (workspace_id, client_id,
commitment_id, evidence_id) values (%s, %s, %s, %s)""",
                (ws, cid, commitment["id"], eid),
            )
            conn.execute(
                """insert into public.client_goal_evidence (workspace_id, client_id,
client_goal_id, evidence_id) values (%s, %s, %s, %s)""",
                (ws, cid, goal["id"], eid),
            )
            conn.execute(
                """insert into public.latent_need_evidence (workspace_id, client_id,
latent_need_id, evidence_id) values (%s, %s, %s, %s)""",
                (ws, cid, need["id"], eid),
            )
            conn.execute(
                """insert into public.outcome (workspace_id, client_id, event, occurred_at)
values (%s, %s, 'renewed', '2025-01-01')""",
                (ws, cid),
            )
            conn.execute(
                """insert into public.client_card (workspace_id, client_id, version, content)
values (%s, %s, 1, '{}'::jsonb), (%s, %s, 2, %s)""",
                (ws, cid, ws, cid, Jsonb(PARTS["cardContent"])),
            )
    with ctx.caller() as conn:
        conn.execute("select public.set_client_policy(%s, 'metadata_only')", (meta,))
    with ctx.pool.connection() as conn:
        assessment(
            conn,
            ws,
            meta,
            day="2025-01-02",
            risk="watch",
            previous="watch",
            drivers=[ASSESSMENT["drivers"][1]],
        )
    base = f"/v1/workspaces/{ws}"
    detail = http.get(f"{base}/clients/{full}", headers=AUTH)
    assert detail.status_code == 200
    body = detail.json()["client"]
    assert body["assessment"]["as_of"] == "2025-01-02"
    assert body["assessment"]["caveats"][0]["code"] == "participant_coverage"
    assert body["state_history"] == [dict(as_of="2025-01-01", **{"from": "stable", "to": "watch"})]
    assert body["stakeholders"][0]["is_champion"] is True
    assert body["unknowns"][0]["code"] == "budget_unknown"
    for key in ("commitments", "goals", "needs"):
        assert len(body[key]) == 1 and len(body[key][0]["evidence_ids"]) == 1
    assert len(body["outcomes"]) == 1 and "PRIVATE BODY" not in detail.text
    metadata = http.get(f"{base}/clients/{meta}", headers=AUTH)
    assert metadata.status_code == 200
    body = metadata.json()["client"]
    assert all(body[k] == [] for k in ("commitments", "goals", "needs"))
    assert body["assessment"]["drivers"][0]["evidence_ids"] == []
    assert "quote" not in str(body["assessment"]["drivers"][0]["inputs"])
    assert len(body["outcomes"]) == 1 and "PRIVATE BODY" not in metadata.text
    assert http.get(f"{base}/clients/{excluded}", headers=AUTH).status_code == 404
    assert http.get(f"{base}/clients/{uuid4()}", headers=AUTH).status_code == 404
    assert http.get(f"{base}/clients/nope", headers=AUTH).status_code == 422
    for cid in (churned, prospect):
        assert (
            http.get(f"{base}/clients/{cid}", headers=AUTH).json()["client"]["assessment"] is None
        )


def test_setup_domains_excluded_and_audit_pagination(
    served: tuple[TestClient, ReadContext],
) -> None:
    http, ctx = served
    ws = ctx.identity.workspace_id
    with ctx.pool.connection() as conn:
        cid = seed_client(conn, ws, "Synthetic", policy="excluded")
        conn.execute(
            """insert into public.client_domain (workspace_id, client_id, domain) values
(%s, %s, 'b.example.test'), (%s, %s, 'a.example.test')""",
            (ws, cid, ws, cid),
        )
        conn.execute(
            """insert into public.audit_log (workspace_id, actor_type, action, metadata)
            select %s, 'system', 'synthetic.test', '{}' from generate_series(1, 51)""",
            (ws,),
        )
    base = f"/v1/workspaces/{ws}"
    setup = http.get(f"{base}/setup/clients", headers=AUTH)
    assert setup.status_code == 200
    assert setup.json()["items"][0]["policy"] == "excluded"
    assert setup.json()["items"][0]["domains"] == ["a.example.test", "b.example.test"]
    audit = http.get(f"{base}/audit", headers=AUTH)
    assert audit.status_code == 200 and len(audit.json()["items"]) == 50
    assert audit.json()["next_page"] == 2
    assert http.get(f"{base}/audit?page=2", headers=AUTH).json()["next_page"] is None
    for page in ("0", "01", "201", "x"):
        assert http.get(f"{base}/audit?page={page}", headers=AUTH).status_code == 422


def test_meeting_live_event_and_policy(served: tuple[TestClient, ReadContext]) -> None:
    http, ctx = served
    ws = ctx.identity.workspace_id
    starts = datetime.now(UTC) + timedelta(days=1)
    with ctx.pool.connection() as conn:
        cid = seed_client(conn, ws, "Synthetic meeting")
        rid, _ = source(conn, ws, cid)
        event = conn.execute(
            """insert into public.calendar_event
(workspace_id, source_record_id, client_id, starts_at, duration_min,
status, attendee_count)
values (%s, %s, %s, %s, 30, 'scheduled', 2) returning id""",
            (ws, rid, cid, starts),
        ).fetchone()
        assert event
        conn.execute(
            """insert into public.meeting_brief
(workspace_id, calendar_event_id, client_id, starts_at, basis, content,
version)
values (%s, %s, %s, %s, 'metadata', %s, 'test')""",
            (
                ws,
                event["id"],
                cid,
                starts,
                Jsonb(dict(since_last_meeting=dict(records=3), questions=[], unknowns=[])),
            ),
        )
    path = f"/v1/workspaces/{ws}/meetings/upcoming"
    response = http.get(path, headers=AUTH)
    assert response.status_code == 200 and len(response.json()["items"]) == 1
    assert response.json()["items"][0]["since_last_meeting"]["records"] == 3
    with ctx.pool.connection() as conn:
        conn.execute(
            """update public.calendar_event set status = 'cancelled' where workspace_id =
%s and id = %s""",
            (ws, event["id"]),
        )
    assert http.get(path, headers=AUTH).json()["items"] == []


def test_audit_failure_returns_no_client_data(
    served: tuple[TestClient, ReadContext], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, ctx = served

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic audit unavailable")

    monkeypatch.setattr("firra.local.routes.clients.audit_read", fail)
    response = http.get(f"/v1/workspaces/{ctx.identity.workspace_id}/attention", headers=AUTH)
    assert response.status_code == 500 and "items" not in response.json()


def test_changes_repository_inclusive_window_and_rls(
    served: tuple[TestClient, ReadContext],
) -> None:
    _, ctx = served
    ws = ctx.identity.workspace_id
    today = datetime.now(UTC).date()
    since = today.isoformat()
    with ctx.pool.connection() as conn:
        for name, policy in (
            ("Visible Full", "full"),
            ("Visible Metadata", "metadata_only"),
            ("Hidden Excluded", "excluded"),
        ):
            cid = seed_client(conn, ws, name, policy=policy)
            assessment(
                conn,
                ws,
                cid,
                day=(today - timedelta(days=1)).isoformat(),
                risk="stable",
                previous=None,
                drivers=[],
            )
            assessment(conn, ws, cid, day=since, risk="watch", previous="stable", drivers=[])
            signal = conn.execute(
                """insert into public.signal
(workspace_id, client_id, type, family, severity, occurred_at,
rule_version, basis, inputs, dedupe_key)
values (%s, %s, 'reduced_engagement', 'engagement', 'low', now(),
'test', 'metadata', '{}', 'test') returning id""",
                (ws, cid),
            ).fetchone()
            assert signal
            conn.execute(
                """insert into public.signal_event
(workspace_id, client_id, signal_id, change, severity, as_of,
rule_version)
values (%s, %s, %s, 'opened', 'low', now(), 'test')""",
                (ws, cid, signal["id"]),
            )
    now = datetime.now(UTC)
    with ctx.caller() as conn:
        items = build_changes(
            repository.changes_assessments(conn, WorkspaceId(ws), since, now),
            repository.changes_events(conn, WorkspaceId(ws), since, now),
            repository.visible_clients(conn, WorkspaceId(ws)),
            since,
            now,
        )
    assert len(items) == 4
    assert {i["client_name"] for i in items} == {"Visible Full", "Visible Metadata"}
    assert all(i["evidence_count"] == 0 for i in items)
    assert "quote" not in str(items) and "text" not in str(items)


def test_clients_repository_status_and_old_live_assessment(
    served: tuple[TestClient, ReadContext],
) -> None:
    _, ctx = served
    ws = ctx.identity.workspace_id
    with ctx.pool.connection() as conn:
        active = seed_client(conn, ws, "A Active")
        churned = seed_client(conn, ws, "B Churned", status="churned")
        prospect = seed_client(conn, ws, "C Prospect", status="prospect")
        seed_client(conn, ws, "Excluded", policy="excluded")
        assessment(conn, ws, active, day="2025-01-01", risk="watch", previous="stable", drivers=[])
        replay = conn.execute(
            """insert into public.replay_run (workspace_id, window_start, window_end)
            values (%s, '2025-01-01', '2025-01-03') returning id""",
            (ws,),
        ).fetchone()
        assert replay
        conn.execute(
            """insert into public.account_assessment
(workspace_id, client_id, as_of, run_kind, replay_run_id,
assessment_version, rule_version, risk_state,
state_since, expansion_state, strength)
values (%s, %s, '2025-01-03', 'replay', %s, 'test', 'test', 'high',
'2025-01-03', 'none', 'weak')""",
            (ws, active, replay["id"]),
        )
    with ctx.caller() as conn:
        rows = repository.clients(conn, WorkspaceId(ws), "active")
        assert len(rows) == 1
        assert (
            build_client_list_item(rows[0], rows[0]["assessment"])["assessment"]["risk_state"]
            == "watch"
        )
        assert {r["id"] for r in repository.clients(conn, WorkspaceId(ws), "all")} == {
            active,
            churned,
            prospect,
        }
        assert [r["id"] for r in repository.clients(conn, WorkspaceId(ws), "churned")] == [churned]
