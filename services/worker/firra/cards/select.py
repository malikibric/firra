"""Weekly card eligibility and ordering (P2b D9); no model or database."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from firra.assessment.config import RiskState, risk_rank
from firra.assessment.model import Assessment

MAX_CARDS = 5


@dataclass(frozen=True)
class Candidate:
    client_id: UUID
    client_name: str
    assessment_id: UUID
    assessment: Assessment
    mrr: Decimal
    days_to_notice: int | None
    last_card_week: date | None
    state_before_change: RiskState | None = None

    def changed(self, week: date) -> bool:
        return week <= self.assessment.state_since < week + timedelta(days=7)

    def escalated(self, week: date) -> bool:
        previous = self.state_before_change or self.assessment.previous_risk_state
        return (
            self.changed(week)
            and previous is not None
            and risk_rank(self.assessment.risk_state) > risk_rank(previous)
        )


def select_candidates(
    candidates: list[Candidate], week: date, *, existing: int = 0
) -> list[Candidate]:
    def eligible(c: Candidate) -> bool:
        a = c.assessment
        if a.play == "monitor" or (c.last_card_week is not None and c.last_card_week >= week):
            return False
        age = (week - c.last_card_week).days if c.last_card_week else None
        risk = risk_rank(a.risk_state) >= risk_rank("elevated") and (
            c.changed(week) or age is None or age >= 21
        )
        opportunity = a.play in {"renewal_prep", "expansion_conversation"} and (
            age is None or age >= 28
        )
        return risk or opportunity

    return sorted(
        (c for c in candidates if eligible(c)),
        key=lambda c: (
            -risk_rank(c.assessment.risk_state),
            -int(c.escalated(week)),
            -c.mrr,
            c.days_to_notice if c.days_to_notice is not None else float("inf"),
            str(c.client_id),
        ),
    )[: max(0, MAX_CARDS - existing)]
