"""Runs every scenario of morning_scan_oracle.yaml (T44b) against the real morning-scan source.

Scenarios come from the original task spec, not from Codex's code. Expectations are
never edited to make a function pass; a disagreement goes to the lead and either the brief or the
code changes. The source reads Postgres, so each scenario seeds a fresh local workspace.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
import yaml  # type: ignore[import-untyped]
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from firra.db.pool import create_pool_for
from firra.db.routines import RoutinesRepo
from firra.db.types import WorkspaceId
from firra.routines.model import Candidate
from firra.routines.sources.morning_scan import MorningScan
from tests.conftest import local_settings

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]

ORACLE: dict[str, list[dict[str, Any]]] = yaml.safe_load(
    (Path(__file__).parent / "morning_scan_oracle.yaml").read_text()
)
QUOTE = "The client is unhappy with the recent delivery quality and timeline"


def on(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _id(row: dict[str, Any] | None) -> UUID:
    assert row is not None
    return UUID(str(row["id"]))


class Seed:
    """One workspace's rows, remembering the label -> id mappings the assertions need."""

    def __init__(self, db: Db, name: str) -> None:
        self.db = db
        self.ws = _id(
            db.execute(
                "insert into public.workspace (name) values (%s) returning id", (name,)
            ).fetchone()
        )
        self.clients: dict[str, UUID] = {}
        self.evidence: dict[tuple[UUID, str], UUID] = {}
        self.replay_run: UUID | None = None
        self.records = 0
        self.connection = _id(
            db.execute(
                "insert into public.connection (workspace_id, provider, mode, account_label, "
                "external_account_id) values (%s, 'gmail', 'user', 'oracle', 'oracle') "
                "returning id",
                (self.ws,),
            ).fetchone()
        )

    def client(
        self,
        key: str,
        *,
        status: str = "active",
        policy: str = "full",
        renewal_date: str | date | None = None,
        notice_period_days: int | None = None,
    ) -> UUID:
        client_id = _id(
            self.db.execute(
                "insert into public.client (workspace_id, name, status, policy, renewal_date, "
                "notice_period_days) values (%s, %s, %s::public.client_status, "
                "%s::public.data_policy, %s, %s) returning id",
                (
                    self.ws,
                    key,
                    status,
                    policy,
                    on(renewal_date) if renewal_date else None,
                    notice_period_days,
                ),
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
                "now(), 'in', 10, %s) returning id",
                (self.ws, self.connection, f"m{self.records}", client, QUOTE),
            ).fetchone()
        )
        evidence_id = _id(
            self.db.execute(
                "insert into public.evidence (workspace_id, source_record_id, client_id, quote, "
                'locator) values (%s, %s, %s, %s, \'{"start": 0, "end": 54}\') returning id',
                (self.ws, record, client, QUOTE),
            ).fetchone()
        )
        self.evidence[(client, label)] = evidence_id
        return evidence_id

    def assessment(self, raw: dict[str, Any]) -> UUID:
        client = self.clients[raw["client"]]
        as_of = on(raw["as_of"])
        run_kind = raw.get("run_kind", "live")
        replay_id = None
        if run_kind == "replay":
            if self.replay_run is None:
                self.replay_run = _id(
                    self.db.execute(
                        "insert into public.replay_run (workspace_id, window_start, window_end, "
                        "status) values (%s, %s, %s, 'succeeded') returning id",
                        (self.ws, as_of, as_of),
                    ).fetchone()
                )
            replay_id = self.replay_run
        assessment_id = _id(
            self.db.execute(
                "insert into public.account_assessment (workspace_id, client_id, as_of, run_kind, "
                "replay_run_id, assessment_version, rule_version, risk_state, previous_risk_state, "
                "state_since, expansion_state, strength) values (%s, %s, %s, "
                "%s::public.assessment_run_kind, %s, 't', 't', %s::public.risk_state, "
                "%s::public.risk_state, %s, %s::public.expansion_state, 'weak') returning id",
                (
                    self.ws,
                    client,
                    as_of,
                    run_kind,
                    replay_id,
                    raw["risk_state"],
                    raw.get("previous_risk_state"),
                    on(raw.get("state_since", raw["as_of"])),
                    raw.get("expansion_state", "none"),
                ),
            ).fetchone()
        )
        for label in raw.get("evidence", []):
            self.db.execute(
                "insert into public.assessment_evidence (workspace_id, assessment_id, evidence_id, "
                "client_id) values (%s, %s, %s, %s)",
                (self.ws, assessment_id, self.quote(client, label), client),
            )
        return assessment_id

    def signal(self, raw: dict[str, Any], index: int) -> None:
        client = self.clients[raw["client"]]
        events = raw["events"]
        # An evidence signal must cite evidence at commit (a deferred constraint trigger), so the
        # signal, its events and its links go in one transaction.
        with self.db.transaction():
            signal_id = _id(
                self.db.execute(
                    "insert into public.signal (workspace_id, client_id, type, severity, "
                    "occurred_at, rule_version, basis, inputs, dedupe_key) values (%s, %s, %s, "
                    "%s::public.signal_severity, %s, 't', %s::public.signal_basis, %s, %s) "
                    "returning id",
                    (
                        self.ws,
                        client,
                        raw["type"],
                        raw.get("severity", events[-1]["severity"]),
                        at(events[0]["as_of"]),
                        raw.get("basis", "evidence"),
                        Jsonb(raw["inputs"]) if raw.get("inputs") else None,
                        f"sig{index}",
                    ),
                ).fetchone()
            )
            for event in events:
                self.db.execute(
                    "insert into public.signal_event (workspace_id, client_id, signal_id, change, "
                    "severity, as_of, rule_version) values (%s, %s, %s, "
                    "%s::public.signal_change, %s::public.signal_severity, %s, 't')",
                    (
                        self.ws,
                        client,
                        signal_id,
                        event["change"],
                        event["severity"],
                        at(event["as_of"]),
                    ),
                )
            for label in raw.get("evidence", []):
                self.db.execute(
                    "insert into public.signal_evidence (workspace_id, signal_id, evidence_id, "
                    "client_id) values (%s, %s, %s, %s)",
                    (self.ws, signal_id, self.quote(client, label), client),
                )

    def previous_runs(self, runs: list[dict[str, Any]]) -> None:
        routine_id = _id(
            self.db.execute(
                "insert into public.routine (workspace_id, kind) "
                "values (%s, 'morning_scan') returning id",
                (self.ws,),
            ).fetchone()
        )
        for run in runs:
            self.db.execute(
                "insert into public.routine_run (workspace_id, routine_id, scheduled_for, status) "
                "values (%s, %s, %s, %s::public.routine_run_status)",
                (self.ws, routine_id, on(run["scheduled_for"]), run["status"]),
            )


def seed(db: Db, scenario: dict[str, Any]) -> Seed:
    s = Seed(db, f"oracle-{scenario['id']}")
    for client in scenario["seed"].get("clients", [{"key": "c"}]):
        s.client(
            client["key"],
            status=client.get("status", "active"),
            policy=client.get("policy", "full"),
            renewal_date=client.get("renewal_date"),
            notice_period_days=client.get("notice_period_days"),
        )
    for raw in scenario["seed"].get("assessments", []):
        s.assessment(raw)
    for index, raw in enumerate(scenario["seed"].get("signals", [])):
        s.signal(raw, index)
    if scenario.get("previous_runs"):
        s.previous_runs(scenario["previous_runs"])
    return s


def other_workspace(db: Db, as_of: date) -> Seed:
    """A second workspace with the same shape: nothing from it may appear in the candidates."""
    other = Seed(db, "oracle-other")
    other.client("other")
    other.assessment(
        {
            "client": "other",
            "as_of": as_of.isoformat(),
            "risk_state": "high",
            "previous_risk_state": "stable",
        }
    )
    return other


def describe(
    candidate: Candidate, keys: dict[UUID, str], labels: dict[UUID, str]
) -> tuple[Any, ...]:
    return (
        keys[candidate.client_id],
        candidate.trigger,
        candidate.kind,
        candidate.risk_to,
        candidate.expansion_to,
        candidate.severity,
        frozenset(labels[e] for e in candidate.evidence_ids),
    )


def matches(actual: tuple[Any, ...], expected: dict[str, Any]) -> bool:
    key, trigger, kind, risk_to, expansion_to, severity, evidence = actual
    if (key, trigger, kind) != (expected["client"], expected["trigger"], expected["kind"]):
        return False
    if "risk_to" in expected and risk_to != expected["risk_to"]:
        return False
    if "expansion_to" in expected and expansion_to != expected["expansion_to"]:
        return False
    if "severity" in expected and severity != expected["severity"]:
        return False
    return "evidence" not in expected or evidence == frozenset(expected["evidence"])


def scenarios() -> list[Any]:
    return [pytest.param(s, id=s["id"]) for s in ORACLE["scenarios"]]


@pytest.mark.parametrize("scenario", scenarios())
def test_morning_scan_scenario(scenario: dict[str, Any]) -> None:
    url = local_settings().database_url.get_secret_value()
    as_of = on(scenario["as_of"])
    with (
        psycopg.connect(url, row_factory=dict_row, autocommit=True) as db,
        create_pool_for(url) as pool,
    ):
        s = seed(db, scenario)
        other = other_workspace(db, as_of)
        try:
            candidates = MorningScan().candidates(
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


def test_scenarios_are_well_formed() -> None:
    assert set(ORACLE) == {"scenarios"}
    for scenario in ORACLE["scenarios"]:
        assert isinstance(scenario["id"], str) and scenario["id"]
        assert isinstance(scenario["why"], str) and scenario["why"]
        assert "seed" in scenario and "expect" in scenario
        for expected in scenario["expect"]:
            assert {"client", "trigger", "kind"} <= set(expected), scenario["id"]
