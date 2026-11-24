"""F1 unknowns checklist from the deterministic card facts."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TypedDict

from firra.memory.rules import MemoryConfig


class Unknown(TypedDict):
    code: str
    detail: str


@dataclass(frozen=True)
class CardFacts:
    has_goal: bool
    has_decision_maker: bool
    renewal_date: date | None
    last_result_at: datetime | None
    champion_id: str | None
    last_champion_message_at: datetime | None
    budget_known: bool


def unknowns(card: CardFacts, *, as_of: date, cfg: MemoryConfig) -> list[Unknown]:
    missing: list[Unknown] = []
    if not card.has_goal:
        missing.append({"code": "no_stated_goal", "detail": "No client-stated goal is recorded."})
    if not card.has_decision_maker:
        missing.append({"code": "no_decision_maker", "detail": "No decision maker is identified."})
    if card.renewal_date is None:
        missing.append({"code": "unknown_renewal_date", "detail": "Renewal date is unknown."})
    if card.last_result_at is None or card.last_result_at.date() < as_of - timedelta(
        days=cfg.result_days
    ):
        missing.append(
            {"code": "no_result_90d", "detail": "No recent reported result is recorded."}
        )
    if (
        card.champion_id is None
        or card.last_champion_message_at is None
        or card.last_champion_message_at.date() < as_of - timedelta(days=cfg.champion_contact_days)
    ):
        missing.append(
            {"code": "no_champion_contact_30d", "detail": "No recent champion contact is recorded."}
        )
    if not card.budget_known:
        missing.append({"code": "budget_unknown", "detail": "Client budget is unknown."})
    return missing
