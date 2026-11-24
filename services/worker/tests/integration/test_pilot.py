"""Content-free pilot commands over synthetic local Postgres records."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from firra.assessment.__main__ import main
from firra.db.estimate import EstimateRepo
from firra.db.pool import create_pool_for
from firra.db.replay import ReportRepo
from firra.db.types import WorkspaceId
from tests.conftest import local_settings
from tests.integration.test_replay_run import _client
from tests.integration.test_signals_facts_as_of import World

pytestmark = pytest.mark.integration


def test_estimate_policy_window_scope_and_pilot_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = local_settings().database_url.get_secret_value()
    monkeypatch.setenv("FIRRA_DATABASE_URL", url)
    now = datetime.now(UTC)
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as db:
        w = World(db, now)
        other = World(db, now)
        try:
            w.record(w.ana, now - timedelta(days=2))
            w.record(w.ana, now - timedelta(days=200))
            w.record(w.ana, now - timedelta(days=400))
            w.record(w.ana, now + timedelta(days=2))
            metadata = w.record(w.ana, now - timedelta(days=2))
            db.execute(
                "update public.source_record set policy_at_ingest = 'metadata_only', "
                "body_clean = null where workspace_id = %s and id = %s",
                (w.ws, metadata),
            )
            unmapped = w.record(w.ana, now - timedelta(days=2))
            db.execute(
                "update public.source_record set client_id = null "
                "where workspace_id = %s and id = %s",
                (w.ws, unmapped),
            )
            w.event(now - timedelta(days=2), 3)
            active = w.client
            lost = _client(w, "Synthetic lost", status="churned")
            hidden = _client(w, "Synthetic hidden", policy="excluded")
            limited = _client(w, "Synthetic limited", policy="metadata_only")
            for client in (lost, hidden, limited):
                w.client = client
                w.record(w.ana, now - timedelta(days=2))
            w.client = active
            other.record(other.ana, now - timedelta(days=2))
            with create_pool_for(url) as pool:
                estimates = EstimateRepo(pool)
                assert estimates.counts(workspace_id=WorkspaceId(w.ws), months=12) == sorted(
                    [
                        {"client_id": active, "status": "active", "emails": 2},
                        {"client_id": lost, "status": "churned", "emails": 1},
                    ],
                    key=lambda row: str(row["client_id"]),
                )
                assert (
                    sum(
                        r["emails"]
                        for r in estimates.counts(workspace_id=WorkspaceId(w.ws), months=6)
                    )
                    == 2
                )
                assert estimates.counts(workspace_id=WorkspaceId(uuid4()), months=12) == []
                assert main(["estimate", "--workspace", str(w.ws)]) == 0
                text = capsys.readouterr().out
                assert "last 12 months: 3" in text and "estimate_usd = 0.00507" in text
                assert "Synthetic" not in text and "late again" not in text
                assert str(hidden)[:8] not in text and str(limited)[:8] not in text

                # Run the existing deterministic replay, then exercise stored caveat counts.
                assert (
                    main(
                        [
                            "replay",
                            "--workspace",
                            str(w.ws),
                            "--from",
                            str((now - timedelta(days=14)).date()),
                            "--to",
                            str(now.date()),
                        ]
                    )
                    == 0
                )
                run = UUID(json.loads(capsys.readouterr().out)["replay_run_id"])
                db.execute(
                    "update public.account_assessment set caveats = %s "
                    "where workspace_id = %s and replay_run_id = %s and client_id = %s",
                    (
                        Jsonb(
                            [
                                {"code": "low_data"},
                                {"code": "low_data"},
                                {"code": "coverage_changed"},
                            ]
                        ),
                        w.ws,
                        run,
                        active,
                    ),
                )
                repo = ReportRepo(pool)
                clients = repo.pilot_clients(workspace_id=WorkspaceId(w.ws), run_id=run)
                a = next(c for c in clients if c["client_id"] == active)
                assert a["low_data"] == a["coverage_changed"] == 3
                assert all(c["client_id"] != hidden for c in clients)
                assert {c["client_id"] for c in clients} == {active, lost, limited}
                assert repo.pilot_run(workspace_id=WorkspaceId(other.ws), run_id=run) is None
                assert repo.pilot_clients(workspace_id=WorkspaceId(other.ws), run_id=run) == []
                before = db.execute(
                    "select summary, updated_at from public.replay_run "
                    "where workspace_id = %s and id = %s",
                    (w.ws, run),
                ).fetchone()
                assert main(["pilot-check", "--workspace", str(w.ws), "--run", str(run)]) == 0
                text = capsys.readouterr().out
                assert f"client {str(active)[:8]} (active):" in text
                assert "low_data=3, coverage_changed=3" in text and "losses: 1" in text
                assert "Synthetic" not in text and "late again" not in text
                assert str(hidden)[:8] not in text
                assert (
                    before
                    == db.execute(
                        "select summary, updated_at from public.replay_run "
                        "where workspace_id = %s and id = %s",
                        (w.ws, run),
                    ).fetchone()
                )
        finally:
            db.execute("delete from public.workspace where id = any(%s)", ([w.ws, other.ws],))
