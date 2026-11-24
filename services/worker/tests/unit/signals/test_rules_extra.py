"""Rule boundaries absent from the independent signal oracle."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from firra.signals.config import load_config
from firra.signals.facts import ClientFacts, ExtractionFact, PersonFact, RecordFact
from firra.signals.rules import (
    RULES,
    champion_latency_drift,
    executive_escalation,
    goal_result_mismatch,
    renewal_window,
)

REF = datetime(2026, 9, 30, tzinfo=UTC)


def _facts(**changes: object) -> ClientFacts:
    fields: dict[str, object] = {
        "client_id": uuid4(),
        "status": "active",
        "policy": "full",
        "contract_start": None,
        "renewal_date": None,
        "notice_period_days": None,
        "ref": REF,
    }
    fields.update(changes)
    return ClientFacts(**fields)  # type: ignore[arg-type]


def test_goal_result_parses_commas_decimals_and_includes_day_180() -> None:
    goal_record, result_record = uuid4(), uuid4()
    goal_evidence, result_evidence = uuid4(), uuid4()
    goal = ExtractionFact(
        uuid4(),
        goal_record,
        "goal",
        {"text": "Reach 1,200.5 qualified leads"},
        REF - timedelta(days=180),
        None,
        (goal_evidence,),
    )
    result = ExtractionFact(
        uuid4(),
        result_record,
        "result_reported",
        {"text": "Produced €400.10 in value"},
        REF,
        None,
        (result_evidence,),
    )
    cfg = goal_result_mismatch.GoalResultMismatchConfig.model_validate(
        load_config().rules["goal_result_mismatch"]
    )
    (draft,) = goal_result_mismatch.evaluate(_facts(extractions=(goal, result)), cfg)
    assert draft.dedupe_key == str(goal_record)
    assert set(draft.evidence_ids) == {goal_evidence, result_evidence}
    assert draft.inputs == {"goal": 1200.5, "result": 400.1}


def test_executive_joining_after_complaint_counts_only_on_same_thread() -> None:
    author, executive = uuid4(), uuid4()
    complaint_record, reply_record = uuid4(), uuid4()
    quote = uuid4()
    complaint_at = REF - timedelta(days=10)
    reply_at = REF - timedelta(days=7)
    people = (
        PersonFact(executive, "Executive", True, False, None, complaint_at + timedelta(days=2)),
    )
    complaint = RecordFact(complaint_record, "thread-a", complaint_at, "in", author, "client", True)
    reply = RecordFact(reply_record, "thread-a", reply_at, "in", executive, "client", True)
    extraction = ExtractionFact(
        uuid4(),
        complaint_record,
        "complaint",
        {"subtype": "delay"},
        complaint_at,
        author,
        (quote,),
    )
    cfg = executive_escalation.ExecutiveEscalationConfig.model_validate(
        load_config().rules["executive_escalation"]
    )
    assert (
        len(
            executive_escalation.evaluate(
                _facts(people=people, records=(complaint, reply), extractions=(extraction,)), cfg
            )
        )
        == 1
    )
    other_thread = RecordFact(reply_record, "thread-b", reply_at, "in", executive, "client", True)
    assert (
        executive_escalation.evaluate(
            _facts(people=people, records=(complaint, other_thread), extractions=(extraction,)), cfg
        )
        == []
    )


def test_renewal_window_includes_day_90_but_not_day_91() -> None:
    cfg = renewal_window.RenewalWindowConfig.model_validate(load_config().rules["renewal_window"])
    at_90 = _facts(renewal_date=date(2026, 12, 29))
    at_91 = _facts(renewal_date=date(2026, 12, 30))
    assert len(renewal_window.evaluate(at_90, cfg)) == 1
    assert renewal_window.evaluate(at_91, cfg) == []


def test_champion_reply_requires_immediately_preceding_firm_record() -> None:
    champion, other = uuid4(), uuid4()

    def record(day: int, hour: int, thread: str, sender: str) -> RecordFact:
        return RecordFact(
            uuid4(),
            thread,
            REF + timedelta(days=day, hours=hour),
            "out" if sender == "firm" else "in",
            None if sender == "firm" else champion if sender == "champion" else other,
            "firm" if sender == "firm" else "client",
            True,
        )

    records = (
        record(-150, 9, "a", "firm"),
        record(-150, 14, "a", "champion"),
        record(-120, 9, "b", "firm"),
        record(-120, 14, "b", "champion"),
        record(-100, 9, "c", "firm"),
        record(-100, 14, "c", "champion"),
        record(-40, 9, "d", "firm"),
        record(-39, 9, "d", "other"),
        record(-37, 9, "d", "champion"),
        record(-20, 9, "e", "firm"),
        record(-16, 9, "e", "champion"),
    )
    cfg = champion_latency_drift.ChampionLatencyDriftConfig.model_validate(
        load_config().rules["champion_latency_drift"]
    )
    facts = _facts(people=(PersonFact(champion, None, False, True, None, None),), records=records)
    assert champion_latency_drift.evaluate(facts, cfg) == []


def test_volume_flags_match_the_coverage_guard_catalog() -> None:
    assert {name for name, spec in RULES.items() if spec.uses_volume} == {
        "reduced_engagement",
        "client_initiation_drop",
        "client_reply_latency_drift",
        "participant_narrowing",
        "decision_maker_silence",
        "champion_latency_drift",
    }


@pytest.mark.parametrize("name", sorted(RULES))
def test_every_rule_requires_a_valid_family(name: str) -> None:
    params = load_config().rules[name]
    spec = RULES[name]
    assert spec.config_model.model_validate(params).family == params["family"]
    with pytest.raises(ValidationError, match="family"):
        spec.config_model.model_validate({k: v for k, v in params.items() if k != "family"})
    with pytest.raises(ValidationError, match="family"):
        spec.config_model.model_validate({**params, "family": "unknown"})
