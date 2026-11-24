"""Runs every case of oracle.yaml (T07, written from the T06 catalog) through the engine.

Expectations are never edited to make a rule pass; a disagreement goes to the lead and either the
catalog or the rule changes. Cases of rules that are not implemented yet are skipped, not lost.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest
import yaml  # type: ignore[import-untyped]

from firra.signals.config import SignalsConfig, load_config
from firra.signals.engine import evaluate
from firra.signals.facts import (
    ClientFacts,
    CommitmentFact,
    EventFact,
    ExtractionFact,
    NeedFact,
    PersonFact,
    RecordFact,
)
from firra.signals.model import SignalDraft
from firra.signals.rules import RULES

REF = datetime(2026, 9, 30, tzinfo=UTC)
NAMESPACE = UUID("6f1c2d3e-0000-4000-8000-000000000001")
ORACLE: dict[str, list[dict[str, Any]]] = yaml.safe_load(
    (Path(__file__).parent / "oracle.yaml").read_text()
)


def uid(kind: str, label: str) -> UUID:
    return uuid5(NAMESPACE, f"{kind}:{label}")


def at(day: int, hour: int = 12) -> datetime:
    return REF + timedelta(days=day, hours=hour)


def on(day: int | None) -> date | None:
    return None if day is None else (REF + timedelta(days=day)).date()


def _payload(item: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if "subtype" in item:
        payload["subtype"] = item["subtype"]
    if "tone" in item:
        payload["tone"] = item["tone"]
    if "change" in item:
        payload["change"] = item["change"]
        payload["person_name"] = item.get("person")
    if "text" in item:
        payload["text"] = item["text"]
    for key in ("budget_direction", "initiative_type"):
        if key in item:
            payload[key] = item[key]
    return payload


def build_facts(case: dict[str, Any]) -> ClientFacts:
    client = case["client"]
    people = tuple(
        PersonFact(
            id=uid("person", p["id"]),
            name=p.get("name"),
            is_decision_maker=p.get("decision_maker", False),
            is_champion=p.get("champion", False),
            departed_at=at(p["departed"], 0) if "departed" in p else None,
            first_seen_at=at(p["first_seen"], 0) if "first_seen" in p else None,
        )
        for p in case.get("people", [])
    )
    records: list[RecordFact] = []
    extractions: list[ExtractionFact] = []
    for r in [*case.get("records", []), *case.get("extra_records", [])]:
        sender = r["from"]
        from_firm = sender == "firm"
        when = at(r["day"], r.get("hour", 12))
        record_id = uid("record", r["id"])
        records.append(
            RecordFact(
                id=record_id,
                thread_key=r["thread"],
                occurred_at=when,
                direction="out" if from_firm else "in",
                author_person_id=None if from_firm else uid("person", sender),
                author_side="firm" if from_firm else "client",
                processed=r.get("processed", True),
            )
        )
        for n, item in enumerate(r.get("items", [])):
            extractions.append(
                ExtractionFact(
                    id=uid("extraction", f"{r['id']}:{n}"),
                    record_id=record_id,
                    kind=item["kind"],
                    payload=_payload(item),
                    occurred_at=when,
                    author_person_id=None if from_firm else uid("person", sender),
                    evidence_ids=(uid("evidence", item["evidence"]),),
                )
            )
    commitments = tuple(
        CommitmentFact(
            id=uid("commitment", c["id"]),
            owner_side=c["side"],
            due_date=on(c.get("due")),
            status=c["status"],
            evidence_ids=tuple(uid("evidence", e) for e in c["evidence"]),
        )
        for c in case.get("commitments", [])
    )
    events = tuple(
        EventFact(
            starts_at=at(e["day"], e.get("hour", 12)),
            status=e["status"],
            attendee_count=e.get("attendees"),
        )
        for e in case.get("events", [])
    )
    needs = tuple(
        NeedFact(
            id=uid("need", n["id"]),
            workspace_service_id=None if n["service"] is None else uid("service", n["service"]),
            status=n["status"],
            last_seen_at=at(n["last_seen"], 0),
            mention_count=n["mentions"],
            evidence_ids=tuple(uid("evidence", e) for e in n["evidence"]),
        )
        for n in case.get("needs", [])
    )
    return ClientFacts(
        client_id=uid("client", "c"),
        status=client["status"],
        policy=client["policy"],
        contract_start=on(client.get("contract_start")),
        renewal_date=on(client.get("renewal_date")),
        notice_period_days=client.get("notice_period_days"),
        ref=REF,
        people=people,
        records=tuple(records),
        extractions=tuple(extractions),
        commitments=commitments,
        events=events,
        needs=needs,
    )


def _dedupe_key(raw: str) -> str:
    if not raw.startswith("@"):
        return raw
    kind, _, label = raw[1:].partition(":")
    if kind == "date":
        return on(int(label)).isoformat()  # type: ignore[union-attr]
    return str(uid(kind, label))


@dataclass(frozen=True)
class Shape:
    type: str
    severity: str
    basis: str
    dedupe_key: str
    evidence: frozenset[UUID]


def _expected(case: dict[str, Any]) -> list[Shape]:
    return sorted(
        (
            Shape(
                type=e["type"],
                severity=e["severity"],
                basis=e["basis"],
                dedupe_key=_dedupe_key(e["dedupe_key"]),
                evidence=frozenset(uid("evidence", label) for label in e["evidence"]),
            )
            for e in case["expect"]
        ),
        key=lambda s: (s.type, s.dedupe_key),
    )


def _actual(drafts: list[SignalDraft]) -> list[Shape]:
    return sorted(
        (
            Shape(d.type, d.severity, d.basis, d.dedupe_key, frozenset(d.evidence_ids))
            for d in drafts
        ),
        key=lambda s: (s.type, s.dedupe_key),
    )


def _only(rule: str) -> SignalsConfig:
    base = load_config()
    return SignalsConfig(
        version=base.version,
        rules={name: {**params, "enabled": name == rule} for name, params in base.rules.items()},
    )


def _cases() -> list[Any]:
    out: list[Any] = []
    for rule, cases in ORACLE.items():
        for case in cases:
            if "pending" in case:
                marks = [pytest.mark.skip(reason=f"pending: {case['pending']}")]
            elif rule in RULES:
                marks = []
            else:
                marks = [pytest.mark.skip(reason=f"{rule} not implemented")]
            out.append(pytest.param(rule, case, id=f"{rule}: {case['name']}", marks=marks))
    return out


@pytest.mark.parametrize(("rule", "case"), _cases())
def test_oracle_case(rule: str, case: dict[str, Any]) -> None:
    result = evaluate(build_facts(case), _only(rule), {rule: RULES[rule]})
    assert not result.invalid, f"rule produced invalid drafts: {dict(result.invalid)}"
    assert _actual(result.drafts) == _expected(case)
    for key, want in (case.get("inputs") or {}).items():
        (draft,) = result.drafts  # `inputs` is only used on single-signal cases
        got = (draft.inputs or {}).get(key)
        assert got is not None, f"missing input {key}"
        assert round(float(got), 2) == round(float(want), 2), f"{key}: {got} != {want}"


def test_every_catalog_rule_has_oracle_cases() -> None:
    from firra.signals.config import KNOWN_RULES

    assert set(ORACLE) == set(KNOWN_RULES)
