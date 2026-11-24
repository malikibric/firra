"""A high share of recent client meetings were cancelled or rescheduled."""

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window


class MeetingCancellationsConfig(RuleConfig):
    window_days: int = Field(gt=0)
    min_affected: int = Field(gt=0)
    min_share: float = Field(gt=0, le=1)
    affected_statuses: tuple[str, ...]
    severity: Severity
    dedupe_key: str


def evaluate(facts: ClientFacts, cfg: MeetingCancellationsConfig) -> list[SignalDraft]:
    events = [
        event
        for event in facts.events
        if in_window(event.starts_at, facts.ref, newer_than_days=cfg.window_days)
    ]
    affected = [event for event in events if event.status in cfg.affected_statuses]
    if len(affected) < cfg.min_affected or len(affected) / len(events) < cfg.min_share:
        return []
    return [
        SignalDraft(
            family=cfg.family,
            type="meeting_cancellations",
            severity=cfg.severity,
            occurred_at=max(event.starts_at for event in affected),
            basis="metadata",
            dedupe_key=cfg.dedupe_key,
            inputs={"affected": len(affected), "total": len(events)},
        )
    ]


SPEC = RuleSpec(MeetingCancellationsConfig, evaluate)
