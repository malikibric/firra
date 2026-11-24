"""Weekly cards through the task endpoint with a scripted backend and local Postgres."""

import json
from dataclasses import replace
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from firra.app import create_app
from firra.db.llm_usage import LlmUsageRepo
from firra.db.types import WorkspaceId
from firra.llm.models import ModelSet
from firra.llm.service import Llm
from firra.llm.vertex import Embedding, Generation, Usage
from tests.conftest import LOCAL_SECRET, local_settings
from tests.tokens import local_token

pytestmark = pytest.mark.integration
WEEK = date(2026, 9, 28)
GOOD = json.dumps(
    {
        "title": "Review this client",
        "interpretation": "The relationship needs attention.",
        "recommendation": "Call the client to review delivery.",
        "fact_ids": ["f1"],
    }
)
BAD = GOOD.replace("needs attention.", "has lost 99999 dollars.")


class ScriptedBackend:
    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def generate(self, model: str, **kwargs: Any) -> Generation:
        self.calls.append(kwargs["user"])
        return Generation(text=self.answers.pop(0), usage=Usage(input_tokens=100, output_tokens=30))

    def embed(self, model: str, text: str, *, task_type: str, dimensionality: int) -> Embedding:
        raise AssertionError("cards do not embed")


def test_cards_task_retry_drop_dedupe_metadata_and_tenant_scope() -> None:
    settings = local_settings()
    with psycopg.connect(
        settings.database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as db:
        ws = db.execute(
            "insert into public.workspace (name) values ('cards fixture') returning id"
        ).fetchone()
        other = db.execute(
            "insert into public.workspace (name) values ('other cards fixture') returning id"
        ).fetchone()
        assert ws and other
        workspace = ws["id"]
        ids = []
        try:
            connection = db.execute(
                """
                insert into public.connection (workspace_id, provider, mode, account_label) values
                (%s,'email_upload','upload','fixture') returning id
                """,
                (workspace,),
            ).fetchone()
            assert connection
            for i, policy in enumerate(("full", "metadata_only", "full", "excluded")):
                client = db.execute(
                    """
                    insert into public.client (workspace_id,name,policy,mrr) values (%s,%s,%s,%s)
                    returning id
                    """,
                    (workspace, f"Client {i}", policy, 400 - i * 100),
                ).fetchone()
                assert client
                client_id = client["id"]
                ids.append(client_id)
                evidence = []
                if i == 0:
                    record = db.execute(
                        """
                        insert into public.source_record (
                            workspace_id, connection_id, provider, external_id, kind, client_id,
                            policy_at_ingest, occurred_at, direction, participants,
                            body_clean, processing_state)
                        values (%s, %s, 'email_upload', 'card-evidence', 'email', %s,
                            'full', '2026-09-29', 'in', '{}', 'The report is late.', 'done')
                        returning id
                        """,
                        (workspace, connection["id"], client_id),
                    ).fetchone()
                    assert record
                    e = db.execute(
                        """
                        insert into public.evidence
                        (workspace_id,client_id,source_record_id,quote,locator) values
                        (%s,%s,%s,'The report is late.', %s) returning id
                        """,
                        (workspace, client_id, record["id"], Jsonb({"start": 0, "end": 19})),
                    ).fetchone()
                    assert e
                    evidence = [str(e["id"])]
                driver = {
                    "type": "delivery_complaint" if evidence else "client_initiation_drop",
                    "family": "delivery" if evidence else "engagement",
                    "severity": "medium",
                    "counted_severity": "medium",
                    "basis": "evidence" if evidence else "metadata",
                    "dedupe_key": "fixture",
                    "occurred_at": "2026-09-29T00:00:00+00:00",
                    "inputs": None if evidence else {"initiated_30": 1},
                    "evidence_ids": evidence,
                    "primary": True,
                }
                for day in ("2026-09-29", "2026-09-30"):
                    db.execute(
                        """
                        insert into public.account_assessment (
                            workspace_id, client_id, as_of, run_kind, assessment_version,
                            rule_version, risk_state, previous_risk_state, state_since,
                            expansion_state, strength, drivers, caveats, data, play)
                        values (%s, %s, %s, 'live', 'assessment.v1', 'signals.v3',
                            'elevated', 'watch', '2026-09-29', 'none', 'moderate',
                            %s, '[]', '{}', 'fix_first')
                        """,
                        (workspace, client_id, day, Jsonb([driver])),
                    )
            backend = ScriptedBackend([BAD, GOOD, GOOD, BAD, BAD])
            app = create_app(settings)
            with TestClient(app) as http:
                ctx = app.state.task_context
                app.state.task_context = replace(
                    ctx,
                    llm=Llm(
                        backend=backend,
                        models=ModelSet(
                            location="eu",
                            extract=None,
                            synth="gemini-3.8-flash",
                            embed=None,
                            synthdata=None,
                        ),
                        usage=LlmUsageRepo(ctx.pool),
                    ),
                    processing_enabled=True,
                )

                def invoke(
                    ws_id: UUID = workspace, week: date = WEEK, expected_status: str = "succeeded"
                ) -> dict[str, Any]:
                    key = str(uuid4())
                    job_id = ctx.jobs.create(
                        workspace_id=WorkspaceId(ws_id),
                        job_type="synth.cards",
                        idempotency_key=key,
                        payload={"trigger": "manual", "week": week.isoformat()},
                    )
                    assert job_id
                    response = http.post(
                        "/tasks/synth.cards",
                        headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
                        json={
                            "workspace_id": str(ws_id),
                            "job_id": str(job_id),
                            "type": "synth.cards",
                            "idempotency_key": key,
                            "payload": {"trigger": "manual", "week": week.isoformat()},
                        },
                    )
                    assert (
                        response.status_code == 200 and response.json()["status"] == expected_status
                    )
                    row = db.execute(
                        """
                        select progress from public.job where workspace_id=%s order by created_at
                        desc limit 1
                        """,
                        (ws_id,),
                    ).fetchone()
                    assert row
                    return dict(row["progress"])

                progress = invoke()
                assert progress["cards_written"] == 2 and progress["cards_dropped"] == 1
                assert progress["wording_attempts"] == 5 and progress["violations"] == {"number": 1}
                assert "number:interpretation:99999" in backend.calls[1]
                cards = db.execute(
                    "select * from public.decision_card where workspace_id=%s order by priority",
                    (workspace,),
                ).fetchall()
                assert [c["client_id"] for c in cards] == ids[:2]
                assert all(
                    c["locale"] == "en"
                    and c["play"] == "fix_first"
                    and c["assessment_id"]
                    and c["model"] == "gemini-3.8-flash"
                    for c in cards
                )
                assert all(
                    c["prompt_version"] == "cards.v1" and len(c["body"]) <= 2000 for c in cards
                )
                assert "The report is late." not in str(cards[0]["facts"])
                linked = db.execute(
                    """
                    select count(*) as n from public.decision_evidence where workspace_id=%s and
                    client_id=%s
                    """,
                    (workspace, ids[0]),
                ).fetchone()
                assert linked and linked["n"] == 1
                meta = db.execute(
                    """
                    select count(*) as n from public.decision_evidence where workspace_id=%s and
                    client_id=%s
                    """,
                    (workspace, ids[1]),
                ).fetchone()
                assert meta and meta["n"] == 0
                # Only the previously dropped client can be retried; existing cards never reword.
                backend.answers.extend([GOOD])
                rerun = invoke()
                assert rerun["cards_written"] == 1 and len(backend.calls) == 6
                assert invoke()["cards_written"] == 0 and len(backend.calls) == 6
                assert invoke(other["id"])["cards_written"] == 0 and len(backend.calls) == 6
                app.state.task_context = replace(app.state.task_context, processing_enabled=False)
                assert (
                    invoke(week=date(2026, 10, 5))["status"] == "disabled"
                    and len(backend.calls) == 6
                )
                app.state.task_context = replace(app.state.task_context, processing_enabled=True)
                invoke(week=date(2026, 9, 29), expected_status="failed")
        finally:
            db.execute(
                "delete from public.workspace where id = any(%s)", ([workspace, other["id"]],)
            )
