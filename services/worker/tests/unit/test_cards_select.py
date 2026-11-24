"""D9 eligibility boundaries and deterministic ordering."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from firra.assessment.config import Play, RiskState
from firra.assessment.model import Assessment
from firra.cards.persist import render_body
from firra.cards.schema import CardText, Fact
from firra.cards.select import Candidate, select_candidates

WEEK = date(2026, 9, 28)
BASE = Candidate(
    UUID(int=1),
    "Acme",
    UUID(int=2),
    Assessment(
        as_of=WEEK,
        risk_state="elevated",
        previous_risk_state="watch",
        state_since=WEEK,
        expansion_state="none",
        strength="moderate",
        drivers=(),
        caveats=(),
        play="fix_first",
        data={},
    ),
    Decimal(100),
    30,
    None,
)


@pytest.mark.parametrize(
    "play,risk,age,changed,wanted",
    [
        ("monitor", "high", None, True, False),
        ("fix_first", "watch", None, True, False),
        ("fix_first", "elevated", 0, True, False),
        ("fix_first", "elevated", 7, True, True),
        ("fix_first", "elevated", 14, False, False),
        ("fix_first", "elevated", 21, False, True),
        ("fix_first", "elevated", None, False, True),
        ("renewal_prep", "watch", 21, False, False),
        ("renewal_prep", "watch", 28, False, True),
        ("expansion_conversation", "stable", 21, False, False),
        ("expansion_conversation", "stable", 28, False, True),
    ],
)
def test_repeat_and_changed_week_rules(
    play: Play, risk: RiskState, age: int | None, changed: bool, wanted: bool
) -> None:
    a = replace(
        BASE.assessment,
        play=play,
        risk_state=risk,
        state_since=WEEK if changed else WEEK - timedelta(days=7),
    )
    c = replace(
        BASE, assessment=a, last_card_week=WEEK - timedelta(days=age) if age is not None else None
    )
    assert bool(select_candidates([c], WEEK)) == wanted


def test_ranking_risk_escalation_mrr_notice_and_tie_break() -> None:
    high = replace(
        BASE,
        client_id=UUID(int=9),
        mrr=Decimal(1),
        assessment=replace(BASE.assessment, risk_state="high"),
    )
    old = replace(
        BASE,
        client_id=UUID(int=8),
        mrr=Decimal(1000),
        assessment=replace(BASE.assessment, state_since=WEEK - timedelta(days=7)),
    )
    big = replace(BASE, client_id=UUID(int=7), mrr=Decimal(200))
    close = replace(BASE, client_id=UUID(int=6), days_to_notice=1)
    tie = replace(BASE, client_id=UUID(int=5))
    rows = [old, tie, high, BASE, close, big]
    assert [c.client_id.int for c in select_candidates(rows, WEEK)] == [9, 7, 6, 1, 5]
    assert len(select_candidates(rows, WEEK, existing=3)) == 2
    assert select_candidates(rows, WEEK, existing=5) == []
    assert select_candidates(list(reversed(rows)), WEEK) == select_candidates(rows, WEEK)


def test_body_preserves_wording_and_bounds_preview_while_facts_remain_full() -> None:
    facts = [
        Fact(id=f"f{i}", text="A deterministic long fact " * 20, basis="metadata") for i in range(8)
    ]
    card = CardText(
        title="Review", interpretation="I" * 600, recommendation="R" * 600, fact_ids=["f0"]
    )
    body = render_body(facts, card)
    assert len(body) <= 2000 and body.endswith(card.interpretation + "\n\n" + card.recommendation)
    assert len(facts) == 8 and facts[-1].text.endswith("fact ")


def test_escalation_is_from_state_change_not_the_latest_unchanged_assessment() -> None:
    unchanged = replace(
        BASE,
        assessment=replace(BASE.assessment, previous_risk_state="elevated"),
        state_before_change="watch",
    )
    assert unchanged.escalated(WEEK)
    prior_week = replace(
        unchanged, assessment=replace(unchanged.assessment, state_since=WEEK - timedelta(days=7))
    )
    assert not prior_week.escalated(WEEK)
