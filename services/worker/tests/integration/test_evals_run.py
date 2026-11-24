"""Real local ingest and tasks; every model call uses the scripted backend."""

import dataclasses
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from firra.app import create_app
from firra.db.evals import EvalRunRepo, RunState
from firra.db.llm_usage import LlmUsageRepo
from firra.evals import run
from firra.http import ProviderError
from firra.llm.models import ModelSet
from firra.llm.service import Llm
from firra.llm.vertex import Generation, Usage
from firra_contracts import Message
from tests.conftest import LOCAL_SECRET, local_settings
from tests.integration.test_process_records import ANSWERS, CapturingQueue, ScriptedBackend
from tests.tokens import local_token

pytestmark = pytest.mark.integration

TARGET = '<source_record id="target">'
CARD = {
    "title": "Review this account",
    "interpretation": "The facts show a change worth a look.",
    "recommendation": "Talk to the client this week.",
    "fact_ids": ["f1"],
}


class CardAwareBackend(ScriptedBackend):
    """Extraction prompts are scripted as before; the cards prompt gets a valid card, or, when
    ``fail_cards`` is set, the provider's 503 (a capacity problem on the synth model)."""

    fail_cards = False

    def generate(self, model: str, *, user: str, **kwargs: Any) -> Generation:
        if TARGET in user:
            return super().generate(model, user=user, **kwargs)
        if self.fail_cards:
            raise ProviderError(503, "UNAVAILABLE")
        return Generation(text=json.dumps(CARD), usage=Usage(input_tokens=400, output_tokens=40))


@pytest.mark.parametrize(
    ("failure", "expected"),
    [("none", 0), ("threshold", 1), ("budget", 3), ("lost_delivery", 0), ("cards_fail", 0)],
)
def test_pipeline_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, expected: int
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    quote = "A useful fixture praise"
    messages = [
        Message.model_validate(
            {
                "provider": "email_upload",
                "external_id": f"fixture-{i}",
                "thread_key": f"t-{i}",
                "occurred_at": datetime(2026, 9, i + 1, tzinfo=UTC),
                "sender": {"email": "dana.cole@brightpath.example"},
                "to": [{"email": "ana.ruiz@northbeam.example"}],
                "cc": [],
                "bcc": [],
                "body_text": f"{quote} message number {i}.",
            }
        )
        for i in range(3)
    ]
    (corpus / "messages.jsonl").write_text("".join(m.model_dump_json() + "\n" for m in messages))
    (corpus / "events.jsonl").write_text("")
    truth = tmp_path / "truth.json"
    truth.write_text(
        json.dumps(
            {
                "as_of": "2026-09-15",
                "clients": {
                    "juniper": {
                        "name": "Juniper Fintech",
                        "status": "active",
                        "signals": ["renewal_window"],
                        "latent_needs": [],
                        "no_latent_needs": [],
                    },
                    "brightpath": {
                        "name": "Brightpath Software",
                        "status": "active",
                        "signals": ["delivery_complaint"] if failure == "threshold" else [],
                        "latent_needs": [],
                        "no_latent_needs": [],
                        "assessment": {"risk": "stable", "expansion": "none", "play": "monitor"},
                    },
                },
                "forbidden_quotes": [],
                "items": [
                    {"external_id": m.external_id, "kind": "praise", "quote": quote}
                    for m in messages
                ],
            }
        )
    )
    monkeypatch.setattr(run, "TRUTH_PATH", truth)
    monkeypatch.setenv("FIRRA_DATABASE_URL", local_settings().database_url.get_secret_value())
    monkeypatch.setenv("FIRRA_LOCAL_TASK_SECRET", LOCAL_SECRET)
    monkeypatch.setenv("FIRRA_LOCAL_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setitem(
        ANSWERS,
        "fixture praise",
        json.dumps(
            {
                "items": [
                    {
                        "kind": "praise",
                        "speaker": "dana.cole@brightpath.example",
                        "text": "Client praises the work.",
                        "quote": quote,
                    }
                ]
            }
        ),
    )
    queue = CapturingQueue()
    monkeypatch.setattr(run, "LocalQueue", lambda *args: queue)
    if failure in {"lost_delivery", "cards_fail"}:
        # nothing waits three minutes in a test: a job queued for 0.4 s and not worked on is
        # delivered again (the settle loop sleeps 0.2 s of real time per poll)
        monkeypatch.setattr(run, "STALL_SECONDS", 0.4)
    backend = CardAwareBackend()
    backend.fail_cards = failure == "cards_fail"
    lost: list[Any] = []
    seen: list[Any] = []
    now = [0.0]
    budget = Decimal("0.00001") if failure == "budget" else Decimal(5)
    app = create_app(local_settings())
    out = tmp_path / "out"
    with TestClient(app) as client:
        ctx = app.state.task_context
        llm = Llm(
            backend=backend,
            models=ModelSet(
                location="eu",
                extract="gemini-3.5-flash-lite",
                embed="gemini-embedding-001",
                synth="gemini-3.8-flash",
                synthdata=None,
            ),
            usage=LlmUsageRepo(ctx.pool),
            hard_cap_usd=budget,
        )
        app.state.task_context = dataclasses.replace(
            ctx, llm=llm, processing_enabled=True, queue=queue
        )

        def settle(repo: EvalRunRepo) -> RunState:
            while queue.envelopes:
                envelope = queue.envelopes.pop(0)
                if failure == "lost_delivery" and envelope.type == "synth.cards" and not lost:
                    lost.append(envelope)  # the answer never arrives: the job waits queued
                    continue
                seen.append(envelope)
                response = client.post(
                    f"/tasks/{envelope.type}",
                    headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
                    json=envelope.model_dump(mode="json"),
                )
                if failure == "cards_fail" and response.status_code == 503:
                    continue  # requeued by the worker; the runner's watchdog delivers it again
                assert response.status_code == 200
            return repo.state()

        def sleep(seconds: float) -> None:
            now[0] += seconds
            if failure in {"lost_delivery", "cards_fail"}:
                time.sleep(0.2)

        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"status": "ok"})
            )
        ) as http:
            code = run.run_evaluation(
                run.RunOptions(out, yes=True, budget_usd=budget, corpus_dir=corpus),
                http=http,
                settle=settle,
                clock=lambda: now[0],
                sleep=sleep,
            )
    assert code == expected
    summary = json.loads((out / "run.json").read_text())
    workspace_id = summary["workspace_id"]
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as db:
        try:
            row = db.execute(
                "select llm_budget_usd_month from public.workspace where id = %s", (workspace_id,)
            ).fetchone()
            assert row and row["llm_budget_usd_month"] == budget.quantize(Decimal("0.01"))
            assert summary["llm_usage"]["input_tokens"] == {"budget": 500, "cards_fail": 1500}.get(
                failure, 1900
            )
            assert quote not in (out / "run.json").read_text()
            if failure == "budget":
                assert summary["failed_jobs"] == [
                    {"type": "process.records", "error_class": "budget_exceeded"}
                ]
                assert summary["pending"] == 2 and not (out / "eval.json").exists()
            else:
                assert (out / "eval.json").is_file() and (out / "eval.md").is_file()
                replay = json.loads((out / "eval.json").read_text())["replay"]
                written = db.execute(
                    "select progress->>'cards_written' as written from public.job "
                    "where workspace_id = %s and type = 'synth.cards'",
                    (workspace_id,),
                ).fetchone()
                # The card is for Juniper (renewal prep); truth lists it, so it is scored.
                if failure != "cards_fail":
                    assert written and written["written"] == "1"
                tags = db.execute(
                    "select service_tags from public.client where workspace_id = %s "
                    "and name = 'Pinecrest Education'",
                    (workspace_id,),
                ).fetchone()
                # A firm tags its clients with names from its service catalog.
                assert tags and tags["service_tags"] == ["SEO & content"]
                later = db.execute(
                    "select count(*)::int as n from public.account_assessment "
                    "where workspace_id = %s and run_kind = 'live' and as_of > '2026-09-15'",
                    (workspace_id,),
                ).fetchone()
                # The unpinned follow-up assessed today; cards must not rest on a later state.
                assert later and later["n"] == 0
                cards_checked = json.loads((out / "eval.json").read_text())["cards_checked"]
                assert cards_checked == (0 if failure == "cards_fail" else 1)
                assert summary["cards_stage"] == ("failed" if failure == "cards_fail" else "ok")
                assert (replay["risk_checked"], replay["risk_correct"]) == (1, 1)
                assert replay["failures"] == {} and replay["passed"]
                assert "Replay risk state at as_of" in (out / "eval.md").read_text()
                stored = db.execute(
                    "select status::text as status, window_end::text as window_end, step_days "
                    "from public.replay_run where workspace_id = %s",
                    (workspace_id,),
                ).fetchall()
                assert stored == [
                    {"status": "succeeded", "window_end": "2026-09-15", "step_days": 7}
                ]
                assert summary["pending"] == 0
                failed = [{"type": "synth.cards", "error_class": "provider"}]
                assert summary["failed_jobs"] == (failed if failure == "cards_fail" else [])
                pinned = [
                    e
                    for e in seen
                    if e.type == "memory.rebuild" and e.payload.get("trigger") == "sweep"
                ]
                assert len(pinned) == 1 and pinned[0].payload["as_of"] == "2026-09-15"
                rebuild = db.execute(
                    "select progress from public.job where workspace_id = %s "
                    "and type = 'memory.rebuild' and payload->>'trigger' = 'sweep'",
                    (workspace_id,),
                ).fetchone()
                assert rebuild and rebuild["progress"]["as_of"] == "2026-09-15"
                cards = [e for e in seen if e.type == "synth.cards"]
                if failure != "cards_fail":  # a failing stage is delivered several times
                    expected_payload = [{"trigger": "manual", "week": "2026-09-14"}]
                    assert [e.payload for e in cards] == expected_payload
                children = [
                    e
                    for e in seen
                    if e.type in {"signals.evaluate", "needs.update"}
                    and e.payload.get("as_of") == "2026-09-15"
                ]
                assert {e.type for e in children} == {"signals.evaluate", "needs.update"}
                assert sum(j["count"] for j in summary["jobs"]) == 8
        finally:
            db.execute("delete from public.workspace where id = %s", (workspace_id,))
