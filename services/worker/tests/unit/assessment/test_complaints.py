"""Table-driven cases for the T24 unresolved-complaint definitions."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml  # type: ignore[import-untyped]

from firra.assessment.complaints import unresolved_complaints
from firra.signals.facts import ClientFacts, CommitmentFact, ExtractionFact, RecordFact

CASES: list[dict[str, Any]] = yaml.safe_load(
    (Path(__file__).parent / "complaints_cases.yaml").read_text()
)


def uid(n: int) -> UUID:
    return UUID(int=n)


def build_facts(case: dict[str, Any]) -> ClientFacts:
    ref = datetime.fromisoformat(case["ref"])
    records = tuple(
        RecordFact(
            id=uid(r["id"]),
            thread_key=r.get("thread"),
            occurred_at=ref + timedelta(seconds=r["seconds"]),
            direction=r.get("direction", "in"),
            author_person_id=None,
            author_side=r.get("side", "client"),
            processed=True,
        )
        for r in case.get("records", [])
    )
    by_record = {r.id: r for r in records}
    extractions = tuple(
        ExtractionFact(
            id=uid(e["id"]),
            record_id=uid(e["record"]),
            kind=e["kind"],
            payload=e.get("payload", {}),
            occurred_at=by_record[uid(e["record"])].occurred_at,
            author_person_id=None,
            evidence_ids=tuple(uid(n) for n in e.get("evidence", [])),
        )
        for e in case.get("extractions", [])
    )
    commitments = tuple(
        CommitmentFact(
            id=uid(c["id"]),
            owner_side=c.get("side", "firm"),
            due_date=None,
            status=c["status"],
            evidence_ids=tuple(uid(n) for n in c.get("evidence", [])),
        )
        for c in case.get("commitments", [])
    )
    return ClientFacts(
        client_id=uid(999),
        status="active",
        policy="full",
        contract_start=None,
        renewal_date=None,
        notice_period_days=None,
        ref=ref,
        records=records,
        extractions=extractions,
        commitments=commitments,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_complaints(case: dict[str, Any]) -> None:
    facts = build_facts(case)
    if case.get("error"):
        with pytest.raises(ValueError, match="within_days"):
            unresolved_complaints(facts, within_days=case["within_days"])
        return
    expected = [uid(n) for n in case["expected"]]
    assert [c.id for c in unresolved_complaints(facts, within_days=case["within_days"])] == expected
    reversed_facts = ClientFacts(
        client_id=facts.client_id,
        status=facts.status,
        policy=facts.policy,
        contract_start=facts.contract_start,
        renewal_date=facts.renewal_date,
        notice_period_days=facts.notice_period_days,
        ref=facts.ref,
        records=tuple(reversed(facts.records)),
        extractions=tuple(reversed(facts.extractions)),
        commitments=tuple(reversed(facts.commitments)),
    )
    assert [
        c.id for c in unresolved_complaints(reversed_facts, within_days=case["within_days"])
    ] == expected
