"""Morning scan history boundaries, policies and source-to-card integration."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.db.pool import create_pool_for
from firra.db.routine_changes import RoutineChangesRepo
from firra.db.routines import RoutinesRepo
from firra.db.types import WorkspaceId
from firra.routines.runner import run
from firra.routines.sources.morning_scan import MorningScan
from tests.conftest import local_settings
from tests.integration.test_replay_run import _client
from tests.integration.test_signals_facts_as_of import World

pytestmark = pytest.mark.integration
DAY = date(2026, 10, 5)


@pytest.fixture
def world() -> Iterator[tuple[World, World, RoutinesRepo]]:
    url = local_settings().database_url.get_secret_value()
    with (
        psycopg.connect(url, row_factory=dict_row, autocommit=True) as db,
        create_pool_for(url) as pool,
    ):
        w = World(db, datetime(2026, 10, 5, 12, tzinfo=UTC))
        other = World(db, w.t)
        try:
            yield w, other, RoutinesRepo(pool)
        finally:
            db.execute(
                "delete from public.workspace where id = any(%s::uuid[])", ([w.ws, other.ws],)
            )


def assessment(
    w: World,
    client: UUID,
    day: date,
    risk: str,
    previous: str | None = None,
    expansion: str = "none",
    replay: bool = False,
) -> UUID:
    replay_id = None
    if replay:
        row = w.db.execute(
            "insert into public.replay_run (workspace_id, window_start, window_end) "
            "values (%s, %s, %s) returning id",
            (w.ws, day, day),
        ).fetchone()
        assert row
        replay_id = row["id"]
    row = w.db.execute(
        "insert into public.account_assessment (workspace_id, client_id, as_of, run_kind, "
        "replay_run_id, assessment_version, rule_version, risk_state, previous_risk_state, "
        "state_since, expansion_state, strength, play) values (%s, %s, %s, %s, %s, "
        "'assess.v1', 'signals.v1', %s, %s, %s, %s, 'strong', 'protect') returning id",
        (
            w.ws,
            client,
            day,
            "replay" if replay else "live",
            replay_id,
            risk,
            previous,
            day,
            expansion,
        ),
    ).fetchone()
    assert row
    return UUID(str(row["id"]))


def signal(
    w: World, client: UUID, events: list[tuple[date, str, str]], evidence: UUID | None = None
) -> None:
    with w.db.transaction():
        row = w.db.execute(
            "insert into public.signal (workspace_id, client_id, type, severity, occurred_at, "
            "rule_version, basis, inputs, dedupe_key) "
            "values (%s, %s, 'reduced_engagement', %s, %s, "
            "'signals.v1', %s, '{}', gen_random_uuid()::text) returning id",
            (w.ws, client, events[-1][2], events[0][0], "evidence" if evidence else "metadata"),
        ).fetchone()
        assert row
        if evidence:
            w.db.execute(
                "insert into public.signal_evidence (workspace_id, signal_id, client_id, "
                "evidence_id) values (%s, %s, %s, %s)",
                (w.ws, row["id"], client, evidence),
            )
        for hour, (day, change, severity) in enumerate(events):
            at = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=hour)
            w.db.execute(
                "insert into public.signal_event (workspace_id, client_id, signal_id, "
                "change, severity, as_of, rule_version) values (%s, %s, %s, %s, %s, %s, "
                "'signals.v1')",
                (w.ws, client, row["id"], change, severity, at),
            )


def routine(w: World, repo: RoutinesRepo) -> UUID:
    repo.ensure_routines(workspace_id=WorkspaceId(w.ws))
    row = w.db.execute(
        "select id from public.routine where workspace_id = %s and kind = 'morning_scan'", (w.ws,)
    ).fetchone()
    assert row
    return UUID(str(row["id"]))


def previous_run(w: World, rid: UUID, day: date, status: str) -> None:
    w.db.execute(
        "insert into public.routine_run (workspace_id, routine_id, scheduled_for, status) "
        "values (%s, %s, %s, %s)",
        (w.ws, rid, day, status),
    )


def test_changes_history_and_policy_scope(world: tuple[World, World, RoutinesRepo]) -> None:
    w, other, repo = world
    ws = WorkspaceId(w.ws)
    rid = routine(w, repo)
    changes = RoutineChangesRepo(repo.pool)
    assert changes.since(workspace_id=ws, as_of=DAY) == DAY - timedelta(days=1)
    previous_run(w, rid, DAY - timedelta(days=4), "succeeded")
    previous_run(w, rid, DAY - timedelta(days=1), "failed")
    assert changes.since(workspace_id=ws, as_of=DAY) == DAY - timedelta(days=4)
    evidence = w.evidence(w.record(w.ana, w.t))
    assessment(w, w.client, DAY - timedelta(days=5), "stable")
    assessment(w, w.client, DAY - timedelta(days=4), "watch", "stable", "hypothesis")
    aid = assessment(w, w.client, DAY - timedelta(days=2), "elevated", "watch", "strong")
    w.db.execute(
        "insert into public.assessment_evidence (workspace_id, assessment_id, client_id, "
        "evidence_id) values (%s, %s, %s, %s)",
        (w.ws, aid, w.client, evidence),
    )
    assessment(w, w.client, DAY, "watch", "elevated", "none")
    assessment(w, w.client, DAY + timedelta(days=1), "high", "watch", "strong")
    assessment(w, w.client, DAY - timedelta(days=1), "high", "watch", replay=True)
    metadata = _client(w, "metadata", policy="metadata_only")
    assessment(w, metadata, DAY, "watch", "stable")
    signal(w, metadata, [(DAY, "opened", "low")])
    for columns in ({"policy": "excluded"}, {"status": "churned"}, {"status": "prospect"}):
        client = _client(w, "hidden", **columns)
        assessment(w, client, DAY, "high", "watch")
        signal(w, client, [(DAY, "opened", "high")])
    assessment(other, other.client, DAY, "high", "watch")
    signal(other, other.client, [(DAY, "opened", "high")])
    signal(
        w,
        w.client,
        [
            (DAY - timedelta(days=4), "opened", "low"),
            (DAY - timedelta(days=3), "reopened", "low"),
            (DAY, "severity_changed", "high"),
            (DAY, "severity_changed", "low"),
            (DAY, "resolved", "low"),
        ],
        evidence,
    )
    for threshold in (14, 30):
        _client(
            w, "deadline", renewal_date=DAY + timedelta(days=threshold + 30), notice_period_days=30
        )
    _client(w, "past", renewal_date=DAY - timedelta(days=1), notice_period_days=0)
    _client(w, "missing-period", renewal_date=DAY, notice_period_days=None)
    candidates = MorningScan().candidates(repo, workspace_id=ws, as_of=DAY)
    full = [c for c in candidates if c.client_id == w.client]
    assert [(c.trigger, c.risk_to, c.expansion_to, c.severity) for c in full] == [
        ("risk_up", "elevated", None, None),
        ("expansion_up", None, "strong", None),
        ("risk_down", "watch", None, None),
        ("signal_opened", None, None, "low"),
        ("signal_opened", None, None, "high"),
        ("signal_resolved", None, None, "low"),
    ]
    assert full[0].evidence_ids == full[1].evidence_ids == frozenset({evidence})
    assert all(c.evidence_ids == frozenset({evidence}) for c in full[3:5])
    assert full[5].trigger == "signal_resolved" and full[5].evidence_ids == frozenset()
    meta = [c for c in candidates if c.client_id == metadata]
    assert [c.trigger for c in meta] == ["risk_up", "signal_opened"]
    assert all(not c.evidence_ids for c in meta)
    assert all(c.as_of == DAY for c in candidates)
    assert len(candidates) == 10  # Six full changes, two metadata changes, two notice crossings.
    previous_run(w, rid, DAY, "succeeded")
    tomorrow = MorningScan().candidates(repo, workspace_id=ws, as_of=DAY + timedelta(days=1))
    assert not any(c.trigger == "notice_close" for c in tomorrow)


def test_seven_day_cap(world: tuple[World, World, RoutinesRepo]) -> None:
    w, _, repo = world
    rid = routine(w, repo)
    previous_run(w, rid, DAY - timedelta(days=20), "succeeded")
    for back, old, new in (
        (8, "stable", "watch"),
        (7, "watch", "elevated"),
        (6, "elevated", "high"),
    ):
        assessment(w, w.client, DAY - timedelta(days=back), new, old)
    assert RoutineChangesRepo(repo.pool).since(workspace_id=WorkspaceId(w.ws), as_of=DAY) == (
        DAY - timedelta(days=7)
    )
    candidates = MorningScan().candidates(repo, workspace_id=WorkspaceId(w.ws), as_of=DAY)
    assert len(candidates) == 1 and candidates[0].risk_to == "high"


def test_runner_writes_expected_importance(world: tuple[World, World, RoutinesRepo]) -> None:
    w, other, repo = world
    watch, resolved = _client(w, "watch"), _client(w, "resolved")
    assessment(w, w.client, DAY, "elevated", "watch")
    assessment(w, watch, DAY, "watch", "stable")
    assessment(w, resolved, DAY, "stable")
    signal(w, resolved, [(DAY - timedelta(days=1), "opened", "low"), (DAY, "resolved", "low")])
    rid = routine(w, repo)
    counts = run(repo, workspace_id=WorkspaceId(w.ws), routine_id=rid, scheduled_for=DAY)
    assert counts["items"] == 3 and counts["important"] == 1
    cards = w.db.execute(
        "select client_id, importance::text from public.decision_card where workspace_id = %s",
        (w.ws,),
    ).fetchall()
    assert {r["client_id"]: r["importance"] for r in cards} == {
        w.client: "important",
        watch: "attention",
        resolved: "fyi",
    }
    assert run(repo, workspace_id=WorkspaceId(w.ws), routine_id=rid, scheduled_for=DAY) == {
        "not_claimed": 1
    }
    assert (
        other.db.execute(
            "select id from public.decision_card where workspace_id = %s", (other.ws,)
        ).fetchall()
        == []
    )
