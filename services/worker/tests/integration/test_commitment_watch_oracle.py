"""Runs commitment_watch_oracle.yaml (T46b) against the real commitment-watch source.

Scenarios come from the original task spec, not from Codex's code. Expectations are
never edited to make a function pass; a disagreement goes to the lead and either the brief or the
code changes. The source reads Postgres, so each scenario seeds a fresh local workspace.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
import yaml  # type: ignore[import-untyped]
from psycopg.rows import dict_row

from firra.db.pool import create_pool_for
from firra.db.routines import RoutinesRepo
from firra.db.types import WorkspaceId
from firra.routines import runner
from firra.routines.model import Candidate
from firra.routines.sources.commitment_watch import CommitmentWatch
from tests.conftest import local_settings

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]

ORACLE: dict[str, list[dict[str, Any]]] = yaml.safe_load(
    (Path(__file__).parent / "commitment_watch_oracle.yaml").read_text()
)
QUOTE = "We will ship the migration by Friday and confirm on the call"
CLOSED = {"done", "cancelled"}


def on(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _id(row: dict[str, Any] | None) -> UUID:
    assert row is not None
    return UUID(str(row["id"]))


class Seed:
    """One workspace's rows, remembering the label -> id mappings the assertions need."""

    def __init__(self, db: Db, name: str) -> None:
        self.db = db
        self.as_of = date(2026, 1, 1)
        self.ws = _id(
            db.execute(
                "insert into public.workspace (name) values (%s) returning id", (name,)
            ).fetchone()
        )
        self.clients: dict[str, UUID] = {}
        self.evidence: dict[tuple[UUID, str], UUID] = {}
        self.records = 0
        self.connection = _id(
            db.execute(
                "insert into public.connection (workspace_id, provider, mode, account_label, "
                "external_account_id) values (%s, 'gmail', 'user', 'oracle', 'oracle') "
                "returning id",
                (self.ws,),
            ).fetchone()
        )

    def client(self, key: str, *, status: str = "active", policy: str = "full") -> UUID:
        client_id = _id(
            self.db.execute(
                "insert into public.client (workspace_id, name, status, policy) values "
                "(%s, %s, %s::public.client_status, %s::public.data_policy) returning id",
                (self.ws, key, status, policy),
            ).fetchone()
        )
        self.clients[key] = client_id
        return client_id

    def quote(self, client: UUID, label: str) -> UUID:
        cached = self.evidence.get((client, label))
        if cached is not None:
            return cached
        self.records += 1
        record = _id(
            self.db.execute(
                "insert into public.source_record (workspace_id, connection_id, provider, "
                "external_id, kind, client_id, policy_at_ingest, occurred_at, direction, "
                "word_count, body_clean) values (%s, %s, 'gmail', %s, 'email', %s, 'full', "
                "now(), 'in', 12, %s) returning id",
                (self.ws, self.connection, f"m{self.records}", client, QUOTE),
            ).fetchone()
        )
        evidence_id = _id(
            self.db.execute(
                "insert into public.evidence (workspace_id, source_record_id, client_id, quote, "
                'locator) values (%s, %s, %s, %s, \'{"start": 0, "end": 60}\') returning id',
                (self.ws, record, client, QUOTE),
            ).fetchone()
        )
        self.evidence[(client, label)] = evidence_id
        return evidence_id

    def assessment(self, raw: dict[str, Any]) -> UUID:
        client = self.clients[raw["client"]]
        as_of = on(raw["as_of"])
        return _id(
            self.db.execute(
                "insert into public.account_assessment (workspace_id, client_id, as_of, run_kind, "
                "assessment_version, rule_version, risk_state, previous_risk_state, state_since, "
                "expansion_state, strength, play) values (%s, %s, %s, 'live', 'assess.v1', 't', "
                "%s::public.risk_state, %s::public.risk_state, %s, 'none', "
                "%s::public.evidence_strength, %s) returning id",
                (
                    self.ws,
                    client,
                    as_of,
                    raw["risk_state"],
                    raw.get("previous_risk_state"),
                    on(raw.get("state_since", raw["as_of"])),
                    raw.get("strength", "strong"),
                    raw.get("play", "protect"),
                ),
            ).fetchone()
        )

    def commitment(self, raw: dict[str, Any], index: int) -> UUID:
        client = self.clients[raw["client"]]
        status = raw.get("status", "open")
        stated_at = datetime.combine(self.as_of, time(9, 0), tzinfo=UTC)
        # A commitment must cite evidence at commit (a deferred constraint trigger), so the row and
        # its links go in one transaction.
        with self.db.transaction():
            commitment_id = _id(
                self.db.execute(
                    "insert into public.commitment (workspace_id, client_id, owner_side, text, "
                    "due_date, status, stated_at, closed_at, dedupe_key) values (%s, %s, "
                    "%s::public.person_side, %s, %s, %s::public.commitment_status, %s, %s, %s) "
                    "returning id",
                    (
                        self.ws,
                        client,
                        raw.get("owner_side", "firm"),
                        f"Synthetic commitment {index}",
                        on(raw["due_date"]) if raw.get("due_date") else None,
                        status,
                        stated_at,
                        stated_at if status in CLOSED else None,
                        f"c{index}",
                    ),
                ).fetchone()
            )
            for label in raw.get("evidence", []):
                self.db.execute(
                    "insert into public.commitment_evidence (workspace_id, commitment_id, "
                    "evidence_id, client_id) values (%s, %s, %s, %s)",
                    (self.ws, commitment_id, self.quote(client, label), client),
                )
        return commitment_id

    def previous_runs(self, runs: list[dict[str, Any]]) -> None:
        routine_ids: dict[str, UUID] = {}
        for run in runs:
            kind = run.get("kind", "commitment_watch")
            if kind not in routine_ids:
                routine_ids[kind] = _id(
                    self.db.execute(
                        "insert into public.routine (workspace_id, kind) "
                        "values (%s, %s::public.routine_kind) returning id",
                        (self.ws, kind),
                    ).fetchone()
                )
            self.db.execute(
                "insert into public.routine_run (workspace_id, routine_id, scheduled_for, status) "
                "values (%s, %s, %s, %s::public.routine_run_status)",
                (self.ws, routine_ids[kind], on(run["scheduled_for"]), run["status"]),
            )

    def set_policy(self, key: str, policy: str) -> None:
        # A direct update, not set_client_policy: the derived rows and their evidence must survive,
        # so the scenario exercises the source's own policy filter.
        self.db.execute(
            "update public.client set policy = %s::public.data_policy "
            "where workspace_id = %s and id = %s",
            (policy, self.ws, self.clients[key]),
        )


def seed(db: Db, scenario: dict[str, Any]) -> Seed:
    s = Seed(db, f"oracle-{scenario['id']}")
    s.as_of = on(scenario["as_of"])
    for client in scenario["seed"].get("clients", [{"key": "c"}]):
        s.client(
            client["key"],
            status=client.get("status", "active"),
            policy=client.get("policy", "full"),
        )
    for index, raw in enumerate(scenario["seed"].get("commitments", [])):
        s.commitment(raw, index)
    for change in scenario["seed"].get("policy_changes", []):
        s.set_policy(change["client"], change["policy"])
    if scenario.get("previous_runs"):
        s.previous_runs(scenario["previous_runs"])
    return s


def other_workspace(db: Db, as_of: date) -> Seed:
    """A second workspace whose commitment just became late: nothing from it may appear."""
    other = Seed(db, "oracle-other")
    other.as_of = as_of
    other.client("other")
    other.previous_runs(
        [{"status": "succeeded", "scheduled_for": (as_of - timedelta(days=3)).isoformat()}]
    )
    other.commitment(
        {
            "client": "other",
            "owner_side": "firm",
            "due_date": (as_of - timedelta(days=2)).isoformat(),
            "evidence": ["e1"],
        },
        0,
    )
    return other


def describe(
    candidate: Candidate, keys: dict[UUID, str], labels: dict[UUID, str]
) -> tuple[Any, ...]:
    return (
        keys[candidate.client_id],
        candidate.trigger,
        candidate.kind,
        candidate.owner_side,
        frozenset(labels[e] for e in candidate.evidence_ids),
    )


def matches(actual: tuple[Any, ...], expected: dict[str, Any]) -> bool:
    key, trigger, kind, owner_side, evidence = actual
    if (key, trigger, kind) != (expected["client"], expected["trigger"], expected["kind"]):
        return False
    if owner_side != expected["owner_side"]:
        return False
    return bool(evidence == frozenset(expected["evidence"]))


def scenarios() -> list[Any]:
    return [pytest.param(s, id=s["id"]) for s in ORACLE["scenarios"]]


@pytest.mark.parametrize("scenario", scenarios())
def test_commitment_watch_scenario(scenario: dict[str, Any]) -> None:
    url = local_settings().database_url.get_secret_value()
    as_of = on(scenario["as_of"])
    with (
        psycopg.connect(url, row_factory=dict_row, autocommit=True) as db,
        create_pool_for(url) as pool,
    ):
        s = seed(db, scenario)
        other = other_workspace(db, as_of)
        try:
            candidates = CommitmentWatch().candidates(
                RoutinesRepo(pool), workspace_id=WorkspaceId(s.ws), as_of=as_of
            )
            keys = {client_id: key for key, client_id in s.clients.items()}
            labels = {evidence_id: label for (_, label), evidence_id in s.evidence.items()}
            actual = [describe(c, keys, labels) for c in candidates]
            assert all(c.as_of == as_of for c in candidates), "candidate.as_of must be as_of"

            unused = list(actual)
            for expected in scenario["expect"]:
                for index, candidate in enumerate(unused):
                    if matches(candidate, expected):
                        unused.pop(index)
                        break
                else:
                    pytest.fail(f"no candidate matches {expected}; got {actual}")
            assert not unused, f"unexpected candidates: {unused}"
        finally:
            db.execute(
                "delete from public.workspace where id = any(%s::uuid[])", ([s.ws, other.ws],)
            )


def test_commitment_watch_end_to_end() -> None:
    """Requirement 7: a commitment that just became late reaches the runner and becomes a card."""
    url = local_settings().database_url.get_secret_value()
    as_of = date(2026, 10, 5)
    with (
        psycopg.connect(url, row_factory=dict_row, autocommit=True) as db,
        create_pool_for(url) as pool,
    ):
        s = Seed(db, "oracle-e2e")
        s.as_of = as_of
        try:
            for key in ("at_risk", "stable", "client_owned"):
                s.client(key)
            for key, risk in (
                ("at_risk", "elevated"),
                ("stable", "stable"),
                ("client_owned", "watch"),
            ):
                s.assessment({"client": key, "as_of": as_of.isoformat(), "risk_state": risk})
            s.previous_runs(
                [
                    {
                        "status": "succeeded",
                        "scheduled_for": (as_of - timedelta(days=3)).isoformat(),
                    }
                ]
            )
            due = (as_of - timedelta(days=2)).isoformat()
            s.commitment(
                {"client": "at_risk", "owner_side": "firm", "due_date": due, "evidence": ["e1"]},
                0,
            )
            s.commitment(
                {"client": "stable", "owner_side": "firm", "due_date": due, "evidence": ["e2"]},
                1,
            )
            s.commitment(
                {
                    "client": "client_owned",
                    "owner_side": "client",
                    "due_date": due,
                    "evidence": ["e3"],
                },
                2,
            )

            repo = RoutinesRepo(pool)
            repo.ensure_routines(workspace_id=WorkspaceId(s.ws))
            routine_id = _id(
                db.execute(
                    "select id from public.routine where workspace_id = %s "
                    "and kind = 'commitment_watch'",
                    (s.ws,),
                ).fetchone()
            )
            counts = runner.run(
                repo, workspace_id=WorkspaceId(s.ws), routine_id=routine_id, scheduled_for=as_of
            )
            assert counts.get("items") == 3, counts

            rows = db.execute(
                "select c.name, d.kind::text as kind, d.reason_code, "
                "d.importance::text as importance from public.decision_card d "
                "join public.client c on c.workspace_id = d.workspace_id and c.id = d.client_id "
                "where d.workspace_id = %s",
                (s.ws,),
            ).fetchall()
            assert {(r["name"], r["kind"], r["reason_code"], r["importance"]) for r in rows} == {
                ("at_risk", "commitment_watch", "commitment_late_at_risk", "important"),
                ("stable", "commitment_watch", "commitment_late", "attention"),
                ("client_owned", "commitment_watch", "client_commitment_late", "fyi"),
            }
            at_risk = s.clients["at_risk"]
            links = db.execute(
                "select e.evidence_id from public.decision_evidence e join public.decision_card d "
                "on d.workspace_id = e.workspace_id and d.id = e.decision_card_id "
                "where e.workspace_id = %s and d.client_id = %s",
                (s.ws, at_risk),
            ).fetchall()
            assert [r["evidence_id"] for r in links] == [s.evidence[(at_risk, "e1")]]
        finally:
            db.execute("delete from public.workspace where id = %s", (s.ws,))


def test_scenarios_are_well_formed() -> None:
    assert set(ORACLE) == {"scenarios"}
    for scenario in ORACLE["scenarios"]:
        assert isinstance(scenario["id"], str) and scenario["id"]
        assert isinstance(scenario["why"], str) and scenario["why"]
        assert "seed" in scenario and "expect" in scenario
        for expected in scenario["expect"]:
            assert set(expected) == {
                "client",
                "trigger",
                "kind",
                "owner_side",
                "evidence",
            }, scenario["id"]
            assert expected["trigger"] == "commitment_late"
            assert expected["kind"] == "commitment_watch"
            assert expected["owner_side"] in {"firm", "client"}
