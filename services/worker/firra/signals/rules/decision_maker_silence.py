"""A previously active decision maker stops writing client messages."""

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window


class DecisionMakerSilenceConfig(RuleConfig):
    window_days: int = Field(gt=0)
    baseline_days: int = Field(gt=0)
    min_baseline_messages: int = Field(gt=0)
    severity: Severity


def evaluate(facts: ClientFacts, cfg: DecisionMakerSilenceConfig) -> list[SignalDraft]:
    drafts = []
    for person in sorted(facts.people, key=lambda p: str(p.id)):
        if not person.is_decision_maker or (
            person.departed_at is not None and person.departed_at <= facts.ref
        ):
            continue
        messages = [r for r in facts.client_messages() if r.author_person_id == person.id]
        if any(
            in_window(r.occurred_at, facts.ref, newer_than_days=cfg.window_days) for r in messages
        ):
            continue
        older = [
            r
            for r in messages
            if in_window(
                r.occurred_at,
                facts.ref,
                newer_than_days=cfg.baseline_days,
                up_to_days=cfg.window_days,
            )
        ]
        if len(older) < cfg.min_baseline_messages:
            continue
        last = max(r.occurred_at for r in older)
        drafts.append(
            SignalDraft(
                type="decision_maker_silence",
                family=cfg.family,
                severity=cfg.severity,
                occurred_at=facts.ref,
                basis="metadata",
                dedupe_key=str(person.id),
                inputs={
                    "person_id": str(person.id),
                    "last_message_at": last.isoformat(),
                    "messages_31_180": len(older),
                },
            )
        )
    return drafts


SPEC = RuleSpec(DecisionMakerSilenceConfig, evaluate, uses_volume=True)
