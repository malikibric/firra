"""Every branch of the assessment (P2b D6), the plays (D9) and the coverage guard (D4)."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from firra.assessment.assess import assess, previous_from_row
from firra.assessment.config import PlaysConfig, load_assessment_config, load_plays_config
from firra.assessment.guard import coverage_guard, volume_rule_types
from firra.assessment.model import Assessment, Caveat, PreviousAssessment
from firra.signals.facts import (
    ClientFacts,
    CoverageFact,
    ExtractionFact,
    NeedFact,
    RecordFact,
)
from firra.signals.model import SignalDraft

REF = datetime(2026, 10, 15, 12, tzinfo=UTC)
AUGUST = datetime(2026, 8, 20, 12, tzinfo=UTC)
CFG = load_assessment_config()
PLAYS = load_plays_config()
VOLUME = volume_rule_types()
TOLERANCE = timedelta(days=CFG.coverage.tolerance_days)

FAMILY = {
    "delivery_complaint": "delivery",
    "late_commitment": "delivery",
    "tone_decline": "sentiment",
    "decision_maker_silence": "stakeholder",
    "champion_departure": "stakeholder",
    "new_senior_stakeholder": "stakeholder",
    "scope_cut_talk": "commercial",
    "meeting_cancellations": "meetings",
    "reduced_engagement": "engagement",
    "client_initiation_drop": "engagement",
    "renewal_window": "timing",
    "expansion_need": "expansion",
    "client_initiative": "expansion",
    "budget_growth": "expansion",
}


def draft(
    type_: str,
    severity: str = "medium",
    *,
    days_ago: int = 1,
    key: str | None = None,
    evidence: Sequence[UUID] = (),
    inputs: dict[str, Any] | None = None,
    ref: datetime = REF,
) -> SignalDraft:
    basis = "evidence" if evidence else "metadata"
    return SignalDraft(
        type=type_,
        severity=severity,  # type: ignore[arg-type]
        occurred_at=ref - timedelta(days=days_ago),
        basis=basis,  # type: ignore[arg-type]
        dedupe_key=key or type_,
        family=FAMILY[type_],  # type: ignore[arg-type]
        evidence_ids=tuple(evidence),
        inputs=inputs if inputs is not None else (None if evidence else {"n": 1}),
    )


def renewal(days: int) -> SignalDraft:
    return draft("renewal_window", inputs={"days_to_notice": days})


def messages(n: int, ref: datetime = REF, thread: str = "t") -> tuple[RecordFact, ...]:
    return tuple(
        RecordFact(
            id=uuid4(),
            thread_key=f"{thread}{i % 3}",
            occurred_at=ref - timedelta(days=2 + i * 5),
            direction="in",
            author_person_id=None,
            author_side="client",
            processed=True,
        )
        for i in range(n)
    )


def facts(
    *,
    ref: datetime = REF,
    records: tuple[RecordFact, ...] | None = None,
    extractions: tuple[ExtractionFact, ...] = (),
    needs: tuple[NeedFact, ...] = (),
    coverage: tuple[CoverageFact, ...] = (),
    policy: str = "full",
) -> ClientFacts:
    return ClientFacts(
        client_id=uuid4(),
        status="active",
        policy=policy,  # type: ignore[arg-type]
        contract_start=None,
        renewal_date=None,
        notice_period_days=None,
        ref=ref,
        records=messages(12, ref) if records is None else records,
        extractions=extractions,
        needs=needs,
        coverage=coverage,
    )


def no_complaints(_facts: ClientFacts, *, within_days: int) -> list[ExtractionFact]:
    return []


def run(
    drafts: Sequence[SignalDraft],
    f: ClientFacts | None = None,
    previous: PreviousAssessment | None = None,
    *,
    unresolved: bool = False,
    guard: Sequence[Caveat] = (),
    plays: PlaysConfig = PLAYS,
) -> Assessment:
    f = f or facts()
    complaint = ExtractionFact(uuid4(), uuid4(), "complaint", {}, f.ref, None, ())

    def complaints(_f: ClientFacts, *, within_days: int) -> list[ExtractionFact]:
        return [complaint] if unresolved else []

    return assess(
        drafts,
        f,
        previous,
        CFG,
        plays,
        volume_types=VOLUME,
        unresolved_complaints=complaints,
        guard_caveats=guard,
    )


# --- risk state ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("drafts", "expected"),
    [
        ([], "stable"),
        ([draft("reduced_engagement", "low")], "watch"),
        ([draft("delivery_complaint")], "watch"),
        ([draft("delivery_complaint"), draft("late_commitment", key="c1")], "watch"),
        ([draft("delivery_complaint"), draft("decision_maker_silence", "low")], "elevated"),
        (
            [
                draft("reduced_engagement", "low"),
                draft("decision_maker_silence", "low"),
                draft("scope_cut_talk", "low"),
            ],
            "elevated",
        ),
        ([draft("delivery_complaint"), draft("tone_decline")], "high"),
        ([draft("tone_decline", "high")], "high"),
        ([draft("client_initiative"), draft("budget_growth")], "stable"),
    ],
)
def test_risk_state_combines_families(drafts: list[SignalDraft], expected: str) -> None:
    assert run(drafts).risk_state == expected


@pytest.mark.parametrize(
    ("drafts", "expected"),
    [
        ([renewal(120)], "watch"),
        ([renewal(30)], "watch"),
        ([draft("reduced_engagement", "low"), renewal(30)], "elevated"),
        ([draft("reduced_engagement", "low"), renewal(60)], "watch"),
        ([draft("delivery_complaint"), draft("scope_cut_talk", "low"), renewal(45)], "high"),
    ],
)
def test_renewal_timing_modifier(drafts: list[SignalDraft], expected: str) -> None:
    assert run(drafts).risk_state == expected


def test_holiday_caps_engagement_and_volume_at_low() -> None:
    drafts = [
        draft("reduced_engagement", ref=AUGUST),
        draft("meeting_cancellations", ref=AUGUST),
    ]
    summer = run(drafts, facts(ref=AUGUST))
    assert summer.risk_state == "watch"
    assert summer.data["family_levels"] == {"engagement": "low", "meetings": "low"}
    assert [c.code for c in summer.caveats] == ["holiday_period"]
    autumn = run([draft("reduced_engagement"), draft("meeting_cancellations")])
    assert autumn.risk_state == "high"
    assert not any(c.code == "holiday_period" for c in autumn.caveats)


def test_holiday_period_wraps_the_year_end() -> None:
    new_year = datetime(2027, 1, 3, 12, tzinfo=UTC)
    result = run([draft("reduced_engagement", ref=new_year)], facts(ref=new_year))
    assert any(c.code == "holiday_period" for c in result.caveats)


# --- hysteresis -------------------------------------------------------------------------------


def _prev(state: str, below: int = 0, since: date = date(2026, 9, 1)) -> PreviousAssessment:
    return PreviousAssessment(date(2026, 10, 8), state, since, below)  # type: ignore[arg-type]


def test_escalation_is_immediate() -> None:
    result = run([draft("tone_decline", "high")], previous=_prev("watch"))
    assert (result.risk_state, result.state_since, result.previous_risk_state) == (
        "high",
        REF.date(),
        "watch",
    )


def test_deescalation_waits_for_consecutive_runs() -> None:
    first = run([draft("reduced_engagement", "low")], previous=_prev("elevated"))
    assert (first.risk_state, first.state_since, first.data["below_runs"]) == (
        "elevated",
        date(2026, 9, 1),
        1,
    )
    second = run([draft("reduced_engagement", "low")], previous=_prev("elevated", below=1))
    assert (second.risk_state, second.state_since, second.data["below_runs"]) == (
        "watch",
        REF.date(),
        0,
    )


def test_a_run_back_at_the_state_resets_the_count() -> None:
    same = run(
        [draft("delivery_complaint"), draft("decision_maker_silence", "low")],
        previous=_prev("elevated", below=1),
    )
    assert (same.risk_state, same.state_since, same.data["below_runs"]) == (
        "elevated",
        date(2026, 9, 1),
        0,
    )


def test_previous_from_row() -> None:
    row = {
        "as_of": date(2026, 10, 8),
        "risk_state": "watch",
        "state_since": date(2026, 9, 1),
        "data": {"below_runs": 1},
    }
    assert previous_from_row(row) == PreviousAssessment(
        date(2026, 10, 8), "watch", date(2026, 9, 1), 1
    )


# --- expansion --------------------------------------------------------------------------------


def _need(mentions: int) -> NeedFact:
    return NeedFact(uuid4(), uuid4(), "open", REF - timedelta(days=5), mentions, (uuid4(),))


@pytest.mark.parametrize(
    ("types", "mentions", "expected"),
    [
        ([], 0, "none"),
        (["client_initiative"], 0, "hypothesis"),
        (["new_senior_stakeholder"], 0, "hypothesis"),
        (["client_initiative", "new_senior_stakeholder"], 0, "strong"),
        (["client_initiative", "budget_growth"], 0, "strong"),
        (["expansion_need"], 3, "hypothesis"),
        (["expansion_need"], 4, "strong"),
    ],
)
def test_expansion_state(types: list[str], mentions: int, expected: str) -> None:
    need = _need(mentions)
    drafts = [
        draft(t, key=str(need.id) if t == "expansion_need" else None, evidence=(uuid4(),))
        for t in types
    ]
    assert run(drafts, facts(needs=(need,))).expansion_state == expected


# --- drivers and strength ---------------------------------------------------------------------


def test_drivers_are_ordered_and_only_risk_drivers_are_primary() -> None:
    drafts = [
        draft("client_initiative", evidence=(uuid4(),)),
        draft("reduced_engagement", "low", days_ago=1),
        draft("delivery_complaint", days_ago=5, evidence=(uuid4(),)),
        draft("decision_maker_silence", days_ago=2),
        draft("tone_decline", "high", days_ago=9, evidence=(uuid4(),)),
        draft("scope_cut_talk", "low", days_ago=3, evidence=(uuid4(),)),
    ]
    result = run(drafts)
    assert [d.type for d in result.drivers] == [
        "tone_decline",
        "delivery_complaint",
        "decision_maker_silence",
        "client_initiative",
        "scope_cut_talk",
        "reduced_engagement",
    ]
    assert [d.type for d in result.drivers if d.primary] == [
        "tone_decline",
        "delivery_complaint",
        "decision_maker_silence",
    ]
    assert result.top_driver is not None and result.top_driver.type == "tone_decline"


def test_strength() -> None:
    evidence = uuid4()
    records = messages(12)
    quote = ExtractionFact(
        uuid4(), records[0].id, "complaint", {}, records[0].occurred_at, None, (evidence,)
    )
    two_families = [draft("delivery_complaint", evidence=(evidence,)), draft("reduced_engagement")]
    assert run(two_families, facts(records=records, extractions=(quote,))).strength == "strong"
    assert run(two_families, facts(records=messages(3))).strength == "weak"
    assert (
        run([draft("delivery_complaint"), draft("late_commitment", key="x")]).strength == "moderate"
    )
    a, b = uuid4(), uuid4()
    quotes = tuple(
        ExtractionFact(uuid4(), r.id, "complaint", {}, r.occurred_at, None, (e,))
        for r, e in zip(records[:2], (a, b), strict=True)
    )
    single = [draft("delivery_complaint", evidence=(a, b))]
    assert run(single, facts(records=records, extractions=quotes)).strength == "moderate"
    assert run([draft("delivery_complaint")]).strength == "weak"


# --- caveats ----------------------------------------------------------------------------------


def test_caveats() -> None:
    records = messages(12)
    praise_evidence = uuid4()
    praise = ExtractionFact(
        uuid4(), records[0].id, "praise", {}, records[0].occurred_at, None, (praise_evidence,)
    )
    guard = Caveat("coverage_changed", "x", {"connection_ids": ["c"]})
    result = run(
        [draft("reduced_engagement", "low"), draft("champion_departure", days_ago=20)],
        facts(records=records, extractions=(praise,), policy="metadata_only"),
        guard=[guard],
    )
    codes = [c.code for c in result.caveats]
    assert codes == ["coverage_changed", "recent_positive", "stakeholder_handover", "metadata_only"]
    assert result.caveats[1].data == {"evidence_ids": [str(praise_evidence)]}
    low = run([], facts(records=messages(7)))
    assert [c.code for c in low.caveats] == ["low_data"]
    assert low.caveats[0].data == {"client_messages": 7}


# --- plays ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("drafts", "unresolved", "expected"),
    [
        ([draft("delivery_complaint"), draft("decision_maker_silence", "low")], False, "fix_first"),
        (
            [draft("decision_maker_silence"), draft("reduced_engagement", "low")],
            True,
            "fix_first",
        ),
        (
            [draft("decision_maker_silence"), draft("reduced_engagement", "low")],
            False,
            "stakeholder_remap",
        ),
        ([renewal(30)], False, "renewal_prep"),
        ([renewal(60)], False, "monitor"),
        (
            [draft("reduced_engagement"), draft("scope_cut_talk", "low")],
            False,
            "founder_review_call",
        ),
        (
            [
                draft("client_initiative"),
                draft("budget_growth"),
                draft("reduced_engagement", "low"),
            ],
            False,
            "expansion_conversation",
        ),
        (
            [
                draft("client_initiative"),
                draft("budget_growth"),
                draft("reduced_engagement"),
                draft("scope_cut_talk", "low"),
            ],
            False,
            "founder_review_call",
        ),
        ([draft("reduced_engagement", "low")], True, "monitor"),
    ],
)
def test_plays(drafts: list[SignalDraft], unresolved: bool, expected: str) -> None:
    assert run(drafts, unresolved=unresolved).play == expected


def test_plays_config_must_end_with_an_unconditional_monitor() -> None:
    raw = PLAYS.model_dump(exclude_none=True)
    with pytest.raises(ValueError, match="unconditional monitor"):
        PlaysConfig.model_validate({**raw, "plays": raw["plays"][:-1]})
    with pytest.raises(ValueError, match="unconditional monitor"):
        PlaysConfig.model_validate(
            {**raw, "plays": [*raw["plays"][:-1], {"play": "monitor", "min_risk": "watch"}]}
        )


# --- coverage guard ---------------------------------------------------------------------------


def test_coverage_guard_suppresses_volume_rules_when_a_connection_stopped() -> None:
    stopped, live, later = uuid4(), uuid4(), uuid4()
    drafts = [
        draft("reduced_engagement"),
        draft("delivery_complaint"),
        draft("decision_maker_silence"),
    ]
    broken = facts(
        coverage=(
            CoverageFact(stopped, "revoked", REF - timedelta(days=10)),
            CoverageFact(live, "active", None),
            CoverageFact(later, "error", REF + timedelta(days=1)),
        )
    )
    kept, caveats = coverage_guard(broken, drafts, VOLUME, tolerance=TOLERANCE)
    assert [d.type for d in kept] == ["delivery_complaint"]
    assert caveats[0].code == "coverage_changed"
    assert caveats[0].data == {
        "connection_ids": [str(stopped)],
        "suppressed_types": ["decision_maker_silence", "reduced_engagement"],
    }
    lag = facts(coverage=(CoverageFact(stopped, "active", REF - timedelta(days=2)),))
    assert coverage_guard(lag, drafts, VOLUME, tolerance=TOLERANCE) == (drafts, ())
    fine = facts(coverage=(CoverageFact(live, "active", None),))
    assert coverage_guard(fine, drafts, VOLUME, tolerance=TOLERANCE) == (drafts, ())


def test_assessment_is_deterministic() -> None:
    drafts = [draft("delivery_complaint"), draft("decision_maker_silence", "low")]
    f = facts()
    assert run(drafts, f) == run(list(reversed(drafts)), f)
