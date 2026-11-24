"""Routine source-to-card integration, recovery and tenant/policy isolation."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.db.jobs import ClaimedJob
from firra.db.pool import create_pool_for
from firra.db.routines import RoutinesRepo
from firra.db.types import JobId, WorkspaceId
from firra.routines import runner
from firra.routines.config import load_config
from firra.routines.engine import plan
from firra.routines.model import Candidate
from firra.tasks.context import TaskContext
from firra.tasks.handlers.routines import handle_routines_run
from firra_contracts.generated.tasks.routines_run import RoutinesRunPayload
from tests.conftest import local_settings
from tests.integration.test_signals_facts_as_of import World

pytestmark = pytest.mark.integration
DAY = date(2026, 10, 5)


def test_repository_cards_policies_retries_and_workspace_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = local_settings().database_url.get_secret_value()
    with (
        psycopg.connect(url, row_factory=dict_row, autocommit=True) as db,
        create_pool_for(url) as pool,
    ):
        w = World(db, datetime(2026, 10, 5, 12, tzinfo=UTC))
        other = World(db, w.t)
        ws = WorkspaceId(w.ws)
        repo = RoutinesRepo(pool)
        try:
            clients = {"full": w.client}
            for policy in ("metadata_only", "excluded", "missing"):
                row = db.execute(
                    "insert into public.client (workspace_id, name, policy) "
                    "values (%s, %s, %s) returning id",
                    (w.ws, policy, "full" if policy == "missing" else policy),
                ).fetchone()
                assert row
                clients[policy] = row["id"]
            for client in (w.client, clients["metadata_only"], clients["excluded"], other.client):
                workspace = other.ws if client == other.client else w.ws
                for day, risk in (
                    (DAY - timedelta(days=1), "watch"),
                    (DAY + timedelta(days=1), "high"),
                ):
                    db.execute(
                        "insert into public.account_assessment (workspace_id, client_id, as_of, "
                        "run_kind, assessment_version, rule_version, risk_state, state_since, "
                        "expansion_state, strength, play) values "
                        "(%s, %s, %s, 'live', 'assess.v1', 'signals.v1', %s, %s, "
                        "'none', 'strong', 'protect')",
                        (workspace, client, day, risk, day),
                    )
            db.execute(
                "update public.client set renewal_date = %s, notice_period_days = 30 "
                "where workspace_id = %s and id = %s",
                (DAY + timedelta(days=40), w.ws, w.client),
            )
            repo.ensure_routines(workspace_id=ws)
            repo.ensure_routines(workspace_id=ws)
            routines = db.execute(
                "select id, kind::text from public.routine where workspace_id = %s",
                (w.ws,),
            ).fetchall()
            assert len(routines) == 3
            ids = {r["kind"]: r["id"] for r in routines}
            assert (
                repo.claim_run(
                    workspace_id=ws, routine_id=ids["pre_meeting_brief"], scheduled_for=DAY
                )
                is None
            )
            assert (
                repo.claim_run(
                    workspace_id=WorkspaceId(other.ws),
                    routine_id=ids["morning_scan"],
                    scheduled_for=DAY,
                )
                is None
            )
            due = [r for r in repo.due_routines(now=w.t) if r.workspace_id == ws]
            assert {r.routine_id for r in due} == {ids["morning_scan"], ids["commitment_watch"]}
            claimed = repo.claim_run(
                workspace_id=ws, routine_id=ids["morning_scan"], scheduled_for=DAY
            )
            assert claimed and claimed["enabled"]
            run_id = claimed["id"]
            assert (
                repo.claim_run(workspace_id=ws, routine_id=ids["morning_scan"], scheduled_for=DAY)
                is None
            )
            repo.finish_run(
                workspace_id=ws, run_id=run_id, status="failed", counts={}, error_class="ValueError"
            )
            retried = repo.claim_run(
                workspace_id=ws, routine_id=ids["morning_scan"], scheduled_for=DAY
            )
            assert retried and retried["id"] == run_id
            for minutes in (14, 16):
                db.execute(
                    "update public.routine_run set started_at = now() - make_interval(mins => %s) "
                    "where workspace_id = %s and id = %s",
                    (minutes, w.ws, run_id),
                )
                reclaimed = repo.claim_run(
                    workspace_id=ws, routine_id=ids["morning_scan"], scheduled_for=DAY
                )
                if minutes == 14:
                    assert reclaimed is None
                else:
                    assert reclaimed and reclaimed["id"] == run_id
            evidence = w.evidence(w.record(w.ana, w.t))
            candidates = {
                client: [
                    Candidate(
                        client,
                        "state_change",
                        "risk_up",
                        DAY,
                        risk_to="elevated",
                        evidence_ids=frozenset({evidence}),
                    )
                ]
                for client in (*clients.values(), other.client)
            }
            contexts = repo.contexts(workspace_id=ws, client_ids=list(candidates), as_of=DAY)
            assert set(contexts) == {w.client, clients["metadata_only"], clients["missing"]}
            assert contexts[w.client].risk == "watch"
            assert contexts[w.client].days_to_notice == 10
            assert repo.latest_play(workspace_id=ws, client_id=w.client, as_of=DAY) == {
                "play": "protect",
                "strength": "strong",
            }
            assert repo.latest_play(workspace_id=ws, client_id=other.client, as_of=DAY) is None
            planned = plan(
                candidates,
                contexts,
                repo.priors(workspace_id=ws, client_ids=list(candidates)),
                repo.important_used(workspace_id=ws, week_start=DAY),
                load_config(),
                DAY,
            )
            repo.finish_run(
                workspace_id=ws,
                run_id=run_id,
                status="failed",
                counts={},
                error_class="RuntimeError",
            )
            source = MagicMock()
            source.candidates.return_value = [c for items in candidates.values() for c in items]
            monkeypatch.setitem(runner.SOURCES, "morning_scan", source)
            ctx = cast(TaskContext, MagicMock(pool=pool))
            job = ClaimedJob(ws, JobId(UUID(int=1)), "routines.run", 1, 5)
            payload = RoutinesRunPayload(routine_id=ids["morning_scan"], scheduled_for=DAY)
            counts = handle_routines_run(ctx, job, payload)
            assert counts == {**planned.counts, "skipped_no_assessment": 1}
            assert db.execute(
                "select status::text, counts, error_class from public.routine_run "
                "where workspace_id = %s and id = %s",
                (w.ws, run_id),
            ).fetchone() == {"status": "succeeded", "counts": counts, "error_class": None}
            source.candidates.assert_called_once()
            assert handle_routines_run(ctx, job, payload) == {"not_claimed": 1}
            assert source.candidates.call_count == 1
            # Card dedupe also holds if the writer is reached again after recovery.
            decisions = tuple(
                replace(d, evidence_ids=frozenset({evidence})) for d in planned.decisions
            )
            assert repo.write_cards(
                workspace_id=ws, decisions=decisions, run_id=run_id, as_of=DAY
            ) == {"written": 0, "skipped_no_assessment": 1}
            cards = db.execute(
                "select * from public.decision_card where workspace_id = %s order by priority",
                (w.ws,),
            ).fetchall()
            assert len(cards) == 2
            for card in cards:
                assert card["importance"] == "important"
                assert card["week"] == DAY and card["surfaced_on"] == DAY
                assert card["routine_run_id"] == run_id
                assert card["dedupe_key"] == f"routine:state_change:{card['client_id']}:{DAY}"
                assert (card["model"], card["prompt_version"], card["locale"]) == (
                    "deterministic",
                    "facts.v1",
                    "en",
                )
                links = db.execute(
                    "select evidence_id from public.decision_evidence "
                    "where workspace_id = %s and decision_card_id = %s",
                    (w.ws, card["id"]),
                ).fetchall()
                if card["client_id"] == clients["metadata_only"]:
                    assert links == [] and card["inputs"]["evidence_ids"] == []
                    assert all(f["basis"] == "metadata" for f in card["facts"])
                else:
                    assert [r["evidence_id"] for r in links] == [evidence]
                    assert all(f["basis"] == "evidence" for f in card["facts"])
            assert repo.important_used(workspace_id=ws, week_start=DAY) == 2
            priors = repo.priors(workspace_id=ws, client_ids=list(candidates))
            assert priors[w.client][0].evidence_ids == frozenset({evidence})
            assert priors[clients["metadata_only"]][0].evidence_ids == frozenset()
            assert (
                repo.claim_run(workspace_id=ws, routine_id=ids["morning_scan"], scheduled_for=DAY)
                is None
            )
            assert ids["morning_scan"] not in {
                r.routine_id for r in repo.due_routines(now=w.t) if r.workspace_id == ws
            }
            assert (
                db.execute(
                    "select id from public.decision_card where workspace_id = %s", (other.ws,)
                ).fetchall()
                == []
            )
            db.execute(
                "delete from public.routine_run where workspace_id = %s and id = %s", (w.ws, run_id)
            )
            assert repo.priors(workspace_id=ws, client_ids=list(candidates)) == priors
            assert (
                db.execute(
                    "select id from public.routine_run where workspace_id = %s", (other.ws,)
                ).fetchall()
                == []
            )
        finally:
            db.execute(
                "delete from public.workspace where id = any(%s::uuid[])", ([w.ws, other.ws],)
            )
