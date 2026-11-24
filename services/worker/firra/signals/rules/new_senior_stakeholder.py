"""A decision maker newly appeared on an established client account."""

from datetime import timedelta

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window, newest


class NewSeniorStakeholderConfig(RuleConfig):
    window_days: int = Field(gt=0)
    min_client_age_days: int = Field(gt=0)
    min_history_days: int = Field(gt=0)
    severity: Severity


def _name(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def evaluate(facts: ClientFacts, cfg: NewSeniorStakeholderConfig) -> list[SignalDraft]:
    if facts.contract_start is None or facts.ref.date() - facts.contract_start <= timedelta(
        days=cfg.min_client_age_days
    ):
        return []
    earliest = min(
        (r.occurred_at for r in facts.records if r.occurred_at <= facts.ref), default=None
    )
    drafts = []
    for person in sorted(facts.people, key=lambda p: str(p.id)):
        if not (
            person.is_decision_maker
            and person.first_seen_at is not None
            and earliest is not None
            and person.first_seen_at - earliest >= timedelta(days=cfg.min_history_days)
            and in_window(person.first_seen_at, facts.ref, newer_than_days=cfg.window_days)
        ):
            continue
        matches = newest(
            [
                item
                for item in facts.extractions
                if item.kind == "stakeholder_change"
                and item.payload.get("change") in {"joined", "new_role"}
                and _name(person.name)
                and _name(item.payload.get("person_name")) == _name(person.name)
                and item.occurred_at <= facts.ref
                and item.evidence_ids
            ]
        )
        evidence = matches[0].evidence_ids if matches else ()
        drafts.append(
            SignalDraft(
                family=cfg.family,
                type="new_senior_stakeholder",
                severity=cfg.severity,
                occurred_at=person.first_seen_at,
                basis="evidence" if evidence else "metadata",
                dedupe_key=str(person.id),
                evidence_ids=evidence,
                inputs=None
                if evidence
                else {
                    "person_id": str(person.id),
                    "first_seen_at": person.first_seen_at.isoformat(),
                },
            )
        )
    return drafts


SPEC = RuleSpec(NewSeniorStakeholderConfig, evaluate)
