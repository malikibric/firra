"""The account champion recently departed, citing a verified statement when available."""

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window, newest


class ChampionDepartureConfig(RuleConfig):
    window_days: int = Field(gt=0)
    severity: Severity


def _name(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def evaluate(facts: ClientFacts, cfg: ChampionDepartureConfig) -> list[SignalDraft]:
    drafts = []
    for person in sorted(facts.people, key=lambda p: str(p.id)):
        if not (
            person.is_champion
            and person.departed_at is not None
            and in_window(person.departed_at, facts.ref, newer_than_days=cfg.window_days)
        ):
            continue
        matches = newest(
            [
                item
                for item in facts.extractions
                if item.kind == "stakeholder_change"
                and item.payload.get("change") == "left"
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
                type="champion_departure",
                severity=cfg.severity,
                occurred_at=person.departed_at,
                basis="evidence" if evidence else "metadata",
                dedupe_key=str(person.id),
                evidence_ids=evidence,
                inputs=None
                if evidence
                else {
                    "person_id": str(person.id),
                    "departed_at": person.departed_at.isoformat(),
                },
            )
        )
    return drafts


SPEC = RuleSpec(ChampionDepartureConfig, evaluate)
