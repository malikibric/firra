"""Synthetic local database proof of isolation, policy and content-free reporting."""

import json
from datetime import UTC, date, datetime

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from firra.db.learning import LearningRepo
from firra.db.pool import create_pool_for
from firra.db.types import WorkspaceId
from firra.learning.__main__ import main
from tests.conftest import local_settings
from tests.integration.test_replay_run import _client
from tests.integration.test_signals_facts_as_of import World

pytestmark = pytest.mark.integration


def test_learning_isolation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = local_settings().database_url.get_secret_value()
    monkeypatch.setenv("FIRRA_DATABASE_URL", url)
    as_of = date(2026, 10, 3)
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as db:
        w, other = World(db, datetime.now(UTC)), World(db, datetime.now(UTC))
        try:
            hidden = _client(w, "excluded", policy="excluded")
            for ws, client, family in [
                (w.ws, w.client, "engagement"),
                (w.ws, hidden, "delivery"),
                (other.ws, other.client, "stakeholders"),
            ]:
                for day in (1, 2):
                    db.execute(
                        "insert into public.account_assessment (workspace_id, client_id, as_of, "
                        "run_kind, assessment_version, rule_version, risk_state, state_since, "
                        "expansion_state, strength, drivers) values (%s, %s, %s, 'live', "
                        "'fixture', 'fixture', 'high', %s, 'none', 'weak', %s)",
                        (
                            ws,
                            client,
                            date(2026, 1, day),
                            date(2026, 1, 1),
                            Jsonb(
                                [
                                    {"family": family, "primary": True},
                                    {"family": "timing", "primary": False},
                                ]
                            ),
                        ),
                    )
                db.execute(
                    "insert into public.outcome (workspace_id, client_id, event, occurred_at, "
                    "note) values (%s, %s, 'churned', '2026-03-01', 'PRIVATE NOTE')",
                    (ws, client),
                )
                card = db.execute(
                    "insert into public.decision_card (workspace_id, client_id, week, kind, "
                    "title, body, locale, score, priority, inputs, model, prompt_version, "
                    "dedupe_key, play, facts, interpretation, recommendation, strength, "
                    "created_at) values (%s, %s, '2026-01-05', 'risk', 'PRIVATE TITLE', "
                    "'PRIVATE BODY', 'en', 1, 1, '{}', 'fixture', 'fixture', %s, "
                    "'monitor', %s, 'fixture', 'fixture', "
                    "'weak', '2026-01-05') returning id",
                    (ws, client, family, Jsonb([{"basis": "metadata"}])),
                ).fetchone()
                assert card is not None
                db.execute(
                    "insert into public.decision_feedback (workspace_id, client_id, "
                    "decision_card_id, user_id, verdict, note, updated_at) "
                    "values (%s, %s, %s, %s, 'useful', 'PRIVATE FEEDBACK', '2026-01-06')",
                    (ws, client, card["id"], w.ana),
                )
            with create_pool_for(url) as pool:
                repo = LearningRepo(pool)
                assert len(repo.feedback(workspace_id=WorkspaceId(w.ws), as_of=as_of)) == 1
                assert len(repo.outcomes(workspace_id=WorkspaceId(w.ws), as_of=as_of)) == 1
                rows = repo.assessments(workspace_id=WorkspaceId(w.ws), as_of=as_of)
                assert len(rows) == 2 and rows[0].primary_families == ("engagement",)
                assert (
                    len(repo.assessments(workspace_id=WorkspaceId(w.ws), as_of=date(2026, 1, 1)))
                    == 1
                )
                assert repo.feedback(workspace_id=WorkspaceId(w.ws), as_of=date(2025, 1, 1)) == []
                assert repo.outcomes(workspace_id=WorkspaceId(w.ws), as_of=date(2025, 1, 1)) == []
            args = ["report", "--workspace", str(w.ws), "--as-of", as_of.isoformat()]
            assert main([*args, "--json"]) == 0
            text = capsys.readouterr().out
            result = json.loads(text)
            assert result["precision"][0]["confirmed"] == 1
            assert result["usefulness"][0]["n"] == 1
            assert main(args) == 0
            text += capsys.readouterr().out
            assert all(
                value not in text
                for value in (
                    str(other.ws),
                    str(hidden),
                    "PRIVATE",
                    "delivery",
                    "stakeholders",
                    "timing",
                )
            )
        finally:
            db.execute("delete from public.workspace where id = any(%s)", ([w.ws, other.ws],))
