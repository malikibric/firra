"""Runs oracle_multichannel.yaml (L2S-10, written from the multichannel spec).

A self-contained copy of the oracle.yaml runner idiom, extended only with the
spec's new fact fields (source, timezone, owner_person_ids) and the coverage
guard from firra.assessment.guard — suppression and caveats live there, not in
drafts. Cases needing fields the engine does not have yet carry
``pending: L2S-09`` and skip at collection, so the suite is green on main.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest
import yaml  # type: ignore[import-untyped]

from firra.assessment.guard import coverage_guard, volume_rule_types
from firra.signals.config import SignalsConfig, load_config
from firra.signals.engine import evaluate
from firra.signals.facts import (
    ClientFacts,
    CommitmentFact,
    CoverageFact,
    EventFact,
    ExtractionFact,
    PersonFact,
    RecordFact,
)
from firra.signals.model import SignalDraft
from firra.signals.rules import RULES

REF = datetime(2026, 9, 30, tzinfo=UTC)
COVERAGE_TOLERANCE = timedelta(days=3)  # assessment.yaml coverage.tolerance_days
NAMESPACE = UUID("6f1c2d3e-0000-4000-8000-000000000002")
ORACLE: dict[str, Any] = yaml.safe_load(
    (Path(__file__).parent / "oracle_multichannel.yaml").read_text()
)


def uid(kind: str, label: str) -> UUID:
    return uuid5(NAMESPACE, f"{kind}:{label}")


def at(day: int, hour: int = 12, minute: int = 0) -> datetime:
    return REF + timedelta(days=day, hours=hour, minutes=minute)


def on(day: int | None) -> date | None:
    return None if day is None else (REF + timedelta(days=day)).date()


def _payload(item: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if "subtype" in item:
        payload["subtype"] = item["subtype"]
    if "tone" in item:
        payload["tone"] = item["tone"]
    return payload


def build_facts(case: dict[str, Any]) -> ClientFacts:
    owners = case.get("owners", [case["owner"]] if "owner" in case else [])
    owner_ids = frozenset(uid("person", o) for o in owners)
    firm_people = {p["id"] for p in case.get("people", []) if p.get("firm", False)}
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
        if not p.get("firm", False)
    )
    records: list[RecordFact] = []
    extractions: list[ExtractionFact] = []
    for r in [*case.get("records", []), *case.get("extra_records", [])]:
        sender = r["from"]
        if sender == "firm":
            side, author = "firm", None
        elif sender in firm_people:
            side, author = "firm", uid("person", sender)
        elif sender == "owner" or sender in owners:
            side, author = (
                "firm",
                uid("person", sender if sender != "owner" else case.get("owner", sender)),
            )
        else:
            side, author = "client", uid("person", sender)
        when = at(r["day"], r.get("hour", 12), r.get("minute", 0))
        record_id = uid("record", r["id"])
        if "thread" in r and "channel" in r:
            thread = f"{r['channel']}/{r['thread']}"
        elif "thread" in r:
            thread = r["thread"]
        elif "channel" in r:
            thread = r["channel"]
        else:
            thread = None
        kwargs: dict[str, Any] = {
            "id": record_id,
            "thread_key": thread,
            "occurred_at": when,
            "direction": "out" if side == "firm" else "in",
            "author_person_id": author,
            "author_side": side,
            "processed": r.get("processed", True),
        }
        if "source" in r:
            kwargs["source"] = r["source"]
        if "connection" in r:
            kwargs["connection_id"] = uid("connection", r["connection"])
        records.append(RecordFact(**kwargs))
        for n, item in enumerate(r.get("items", [])):
            extractions.append(
                ExtractionFact(
                    id=uid("extraction", f"{r['id']}:{n}"),
                    record_id=record_id,
                    kind=item["kind"],
                    payload=_payload(item),
                    occurred_at=when,
                    author_person_id=author,
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
    facts_kwargs: dict[str, Any] = {
        "client_id": uid("client", "c"),
        "status": case["client"]["status"],
        "policy": case["client"]["policy"],
        "contract_start": on(case["client"].get("contract_start")),
        "renewal_date": on(case["client"].get("renewal_date")),
        "notice_period_days": case["client"].get("notice_period_days"),
        "ref": REF,
        "people": people,
        "records": tuple(records),
        "extractions": tuple(extractions),
        "commitments": commitments,
        "events": events,
        "needs": (),
    }
    if "timezone" in case:
        facts_kwargs["timezone"] = case["timezone"]
    if "owner" in case or "owners" in case:
        facts_kwargs["owner_person_ids"] = owner_ids
    if "connections" in case:
        facts_kwargs["coverage"] = tuple(
            CoverageFact(
                connection_id=uid("connection", c["id"]),
                status=c["status"],
                synced_until=at(c["synced_until"], 0) if "synced_until" in c else None,
            )
            for c in case["connections"]
        )
    return ClientFacts(**facts_kwargs)


@dataclass(frozen=True)
class Shape:
    type: str
    severity: str
    basis: str
    dedupe_key: str
    evidence: frozenset[UUID]


def _dedupe_key(raw: str) -> str:
    if not raw.startswith("@"):
        return raw
    kind, _, label = raw[1:].partition(":")
    if kind == "date":
        return on(int(label)).isoformat()  # type: ignore[union-attr]
    return str(uid(kind, label))


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


def run_case(case: dict[str, Any], rule: str) -> tuple[list[Shape], list[str]]:
    """Evaluate one rule, then the coverage guard; return (shapes, caveat codes)."""
    facts = build_facts(case)
    result = evaluate(facts, _only(rule), {rule: RULES[rule]})
    assert not result.invalid, f"rule produced invalid drafts: {dict(result.invalid)}"
    kept, caveats = coverage_guard(
        facts, result.drafts, volume_rule_types(), tolerance=COVERAGE_TOLERANCE
    )
    return _actual(kept), sorted(c.code for c in caveats)


def _cases() -> list[Any]:
    out: list[Any] = []
    for _section, cases in ORACLE.items():
        if not isinstance(cases, list):
            continue
        for case in cases:
            if not isinstance(case, dict) or "name" not in case or "rule" not in case:
                continue  # shared record anchors, not cases
            rule = case["rule"]
            if "pending" in case:
                marks = [pytest.mark.skip(reason=f"pending: {case['pending']}")]
            elif rule in RULES:
                marks = []
            else:
                marks = [pytest.mark.skip(reason=f"{rule} not implemented")]
            out.append(pytest.param(rule, case, id=f"{rule}: {case['name']}", marks=marks))
    return out


@pytest.mark.parametrize(("rule", "case"), _cases())
def test_multichannel_oracle_case(rule: str, case: dict[str, Any]) -> None:
    shapes, caveats = run_case(case, rule)
    assert shapes == _expected(case)
    assert caveats == sorted(case.get("caveats", []))
    if case.get("inputs"):
        # `inputs` is only used on single-signal cases.
        assert len(shapes) == 1
        facts = build_facts(case)
        result = evaluate(facts, _only(rule), {rule: RULES[rule]})
        kept, _ = coverage_guard(
            facts, result.drafts, volume_rule_types(), tolerance=COVERAGE_TOLERANCE
        )
        (draft,) = kept
        for key, want in case["inputs"].items():
            got = (draft.inputs or {}).get(key)
            assert got is not None, f"missing input {key}"
            if isinstance(want, str):
                assert got == want, f"{key}: {got} != {want}"
            else:
                assert round(float(got), 2) == round(float(want), 2), f"{key}: {got} != {want}"


def test_chat_records_after_ref_change_nothing() -> None:
    """Spec §1 as-of rule for chat: records after ref never change facts at ref."""
    case: dict[str, Any] = {
        "client": {"status": "active", "policy": "full", "contract_start": -500},
        "people": [{"id": "ana"}],
        "timezone": "UTC",
        "records": [
            {"id": "c1", "day": -3, "hour": 10, "channel": "c", "source": "chat", "from": "ana"},
            {"id": "c2", "day": -10, "hour": 10, "channel": "c", "source": "chat", "from": "ana"},
        ],
    }
    try:
        case_shapes = run_case(case, "reduced_engagement")
    except TypeError:
        pytest.skip("pending: L2S-09 (RecordFact.source)")
    future_shapes = run_case(
        {
            **case,
            "extra_records": [
                {"id": "c3", "day": 5, "hour": 10, "channel": "c", "source": "chat", "from": "ana"},
                {
                    "id": "c4",
                    "day": 10,
                    "hour": 10,
                    "channel": "c",
                    "source": "chat",
                    "from": "ana",
                },
            ],
        },
        "reduced_engagement",
    )
    assert case_shapes == future_shapes
