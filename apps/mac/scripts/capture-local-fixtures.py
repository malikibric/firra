"""Capture synthetic Swift decoding fixtures from the existing local route test harness."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from psycopg.types.json import Jsonb
from tests.integration.test_local_client_routes import assessment, seed_client, source
from tests.integration.test_local_routes import AUTH, served
from tests.integration.test_local_routes_decisions import _insert_card
from tests.unit.test_local_evidence_builders import NARRATIVE, TOTALS

out = Path(__file__).resolve().parent.parent / "Tests/FirraKitTests/Fixtures/LocalRoutes"
out.mkdir(parents=True, exist_ok=True)
harness = served.__wrapped__()
http, ctx = next(harness)
try:
    ws = ctx.identity.workspace_id
    base = f"/v1/workspaces/{ws}"
    with ctx.pool.connection() as conn:
        cid = seed_client(conn, ws, "Synthetic Acme")
        assessment(conn, ws, cid, drivers=[])
        rid, eid = source(conn, ws, cid)
        card = _insert_card(conn, ws, cid)
        conn.execute(
            """insert into public.decision_evidence
            (workspace_id,client_id,decision_card_id,evidence_id) values (%s,%s,%s,%s)""",
            (ws, cid, card, eid),
        )
        starts = datetime.now(UTC) + timedelta(days=1)
        event = conn.execute(
            """insert into public.calendar_event (workspace_id,source_record_id,client_id,
            starts_at,duration_min,status,attendee_count) values
            (%s,%s,%s,%s,30,'scheduled',2) returning id""",
            (ws, rid, cid, starts),
        ).fetchone()
        conn.execute(
            """insert into public.meeting_brief
            (workspace_id,calendar_event_id,client_id,starts_at,basis,content,version)
            values (%s,%s,%s,%s,'metadata',%s,'test')""",
            (
                ws,
                event["id"],
                cid,
                starts,
                Jsonb(dict(since_last_meeting=dict(records=3), questions=[], unknowns=[])),
            ),
        )
        run = conn.execute(
            """insert into public.replay_run
            (workspace_id,window_start,window_end,status,finished_at,summary) values
            (%s,'2026-09-01','2026-09-30','succeeded',now(),%s) returning id""",
            (ws, Jsonb(dict(totals=TOTALS))),
        ).fetchone()["id"]
        conn.execute(
            """insert into public.replay_narrative
            (workspace_id,replay_run_id,client_id,version,narrative) values
            (%s,%s,%s,1,%s)""",
            (ws, run, cid, Jsonb(NARRATIVE)),
        )

    def capture(name, path, method="GET", body=None):
        response = http.request(method, path, headers=AUTH, json=body)
        if response.status_code not in (200, 201, 204):
            raise RuntimeError(f"fixture {name}: status {response.status_code}")
        if response.content:
            (out / (name + ".json")).write_text(json.dumps(response.json(), indent=2) + "\n")

    capture("me", "/v1/me")
    capture("status", "/local/status")
    capture("domains", f"{base}/clients/{cid}/domains", "PUT", {"domains": ["acme.example"]})
    capture(
        "service-create",
        f"{base}/services",
        "POST",
        {"name": "Synthetic SEO", "description": "Synthetic service", "keywords": ["seo"]},
    )
    capture("routine-enable", f"{base}/routines/morning_scan", "PUT", {"enabled": False})
    capture("feedback", f"{base}/decisions/{card}/feedback", "POST", {"verdict": "useful"})
    capture(
        "outcome",
        f"{base}/clients/{cid}/outcomes",
        "POST",
        {"event": "renewed", "occurred_at": "2026-10-05"},
    )
    for name, path in {
        "attention": f"{base}/attention",
        "changes": f"{base}/changes",
        "meetings": f"{base}/meetings/upcoming",
        "clients": f"{base}/clients?status=all",
        "detail": f"{base}/clients/{cid}",
        "timeline": f"{base}/clients/{cid}/timeline",
        "evidence-page": f"{base}/clients/{cid}/evidence",
        "evidence": f"{base}/evidence/{eid}",
        "decisions": f"{base}/decisions?status=all",
        "routines": f"{base}/routines",
        "setup-clients": f"{base}/setup/clients",
        "services": f"{base}/services",
        "replay-runs": f"{base}/replay/runs",
        "replay": f"{base}/replay/runs/{run}/clients/{cid}",
        "audit": f"{base}/audit",
    }.items():
        capture(name, path)
finally:
    harness.close()
