"""Commitment watch boundaries, tenant/policy isolation and real runner cards."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.db.pool import create_pool_for
from firra.db.routine_commitments import RoutineCommitmentsRepo
from firra.db.routines import RoutinesRepo
from firra.db.types import WorkspaceId
from firra.routines.runner import run
from firra.routines.sources.commitment_watch import CommitmentWatch
from tests.conftest import local_settings
from tests.integration.test_morning_scan import assessment, previous_run
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


def routine(w: World, repo: RoutinesRepo, kind: str = "commitment_watch") -> UUID:
    repo.ensure_routines(workspace_id=WorkspaceId(w.ws))
    row = w.db.execute(
        "select id from public.routine where workspace_id = %s and kind = %s", (w.ws, kind)
    ).fetchone()
    assert row
    return UUID(str(row["id"]))


def commitment(
    w: World,
    client: UUID,
    key: str,
    due_back: int | None = 1,
    owner: str = "firm",
    status: str = "open",
    stated_back: int = 10,
) -> tuple[UUID, frozenset[UUID]]:
    # Reuse the verified synthetic evidence fixture with a person belonging to this client.
    original_client, original_person = w.client, w.ana
    w.client = client
    w.ana = w._person("Synthetic commitment owner", w.t - timedelta(days=100))
    try:
        cid = w.commitment(
            key,
            w.t - timedelta(days=stated_back),
            w.t - timedelta(days=due_back) if due_back is not None else None,
            w.t if status in ("done", "cancelled") else None,
        )
    finally:
        w.client, w.ana = original_client, original_person
    w.db.execute(
        "update public.commitment set owner_side = %s, status = %s "
        "where workspace_id = %s and id = %s",
        (owner, status, w.ws, cid),
    )
    links = w.db.execute(
        "select evidence_id from public.commitment_evidence "
        "where workspace_id = %s and commitment_id = %s",
        (w.ws, cid),
    ).fetchall()
    return cid, frozenset(r["evidence_id"] for r in links)


def test_history_boundaries_and_isolation(world: tuple[World, World, RoutinesRepo]) -> None:
    w, other, repo = world
    ws = WorkspaceId(w.ws)
    rid = routine(w, repo)
    stored = RoutineCommitmentsRepo(repo.pool)
    assert stored.since(workspace_id=ws, as_of=DAY) == DAY - timedelta(days=1)
    previous_run(w, rid, DAY - timedelta(days=4), "succeeded")
    previous_run(w, rid, DAY - timedelta(days=1), "failed")
    previous_run(w, rid, DAY, "succeeded")
    previous_run(w, rid, DAY + timedelta(days=1), "succeeded")
    previous_run(w, routine(w, repo, "morning_scan"), DAY - timedelta(days=1), "succeeded")
    previous_run(other, routine(other, repo), DAY - timedelta(days=1), "succeeded")
    assert stored.since(workspace_id=ws, as_of=DAY) == DAY - timedelta(days=4)
    expected = []
    for back in (1, 3, 4):
        _, evidence = commitment(w, w.client, f"late-{back}", back)
        expected.append(evidence)
    for key, due_back, status in (
        ("already-late", 5, "open"),
        ("due-today", 0, "open"),
        ("future-due", -1, "open"),
        ("no-date", None, "open"),
        ("done", 1, "done"),
        ("cancelled", 1, "cancelled"),
    ):
        commitment(w, w.client, key, due_back, status=status)
    commitment(w, w.client, "future-statement", 1, stated_back=-1)
    for policy in ("excluded", "metadata_only"):
        client = _client(w, policy)
        commitment(w, client, policy)
        w.db.execute(
            "update public.client set policy = %s where workspace_id = %s and id = %s",
            (policy, w.ws, client),
        )
    for status in ("churned", "prospect"):
        client = _client(w, status, status=status)
        commitment(w, client, status)
    commitment(other, other.client, "other-workspace")
    result = CommitmentWatch().candidates(repo, workspace_id=ws, as_of=DAY)
    assert len(result) == 3 and all(c.client_id == w.client and c.as_of == DAY for c in result)
    assert {c.evidence_ids for c in result} == set(expected)
    assert all(c.kind == "commitment_watch" and c.trigger == "commitment_late" for c in result)


def test_seven_day_cap(world: tuple[World, World, RoutinesRepo]) -> None:
    w, _, repo = world
    rid = routine(w, repo)
    previous_run(w, rid, DAY - timedelta(days=20), "succeeded")
    expected = set()
    for back in (8, 7, 6):
        _, evidence = commitment(w, w.client, f"cap-{back}", back)
        if back <= 7:
            expected.add(evidence)
    assert RoutineCommitmentsRepo(repo.pool).since(workspace_id=WorkspaceId(w.ws), as_of=DAY) == (
        DAY - timedelta(days=7)
    )
    result = CommitmentWatch().candidates(repo, workspace_id=WorkspaceId(w.ws), as_of=DAY)
    assert {c.evidence_ids for c in result} == expected


def test_runner_importance_and_evidence(world: tuple[World, World, RoutinesRepo]) -> None:
    w, other, repo = world
    stable, client_owned = _client(w, "stable"), _client(w, "client-owned")
    expected = {}
    for client, risk, owner, importance in (
        (w.client, "elevated", "firm", "important"),
        (stable, "stable", "firm", "attention"),
        (client_owned, "elevated", "client", "fyi"),
    ):
        assessment(w, client, DAY, risk)
        _, evidence = commitment(w, client, str(client), owner=owner)
        expected[client] = (importance, evidence)
    rid = routine(w, repo)
    counts = run(repo, workspace_id=WorkspaceId(w.ws), routine_id=rid, scheduled_for=DAY)
    assert counts["items"] == 3 and counts["important"] == 1
    cards = w.db.execute(
        "select c.client_id, c.importance::text, c.kind::text, c.inputs, "
        "array(select e.evidence_id from public.decision_evidence e "
        "where e.workspace_id = c.workspace_id and e.decision_card_id = c.id) as evidence_ids "
        "from public.decision_card c where c.workspace_id = %s",
        (w.ws,),
    ).fetchall()
    assert {
        c["client_id"]: (c["importance"], frozenset(c["evidence_ids"])) for c in cards
    } == expected
    assert all(c["kind"] == "commitment_watch" for c in cards)
    assert {c["inputs"]["reason_code"] for c in cards} == {
        "commitment_late_at_risk",
        "commitment_late",
        "client_commitment_late",
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
