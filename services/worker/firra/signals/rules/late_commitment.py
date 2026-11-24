"""Open firm commitments whose due dates were recently missed."""

from datetime import datetime, time

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window


class LateCommitmentConfig(RuleConfig):
    window_days: int = Field(gt=0)
    high_overdue_days: int = Field(gt=0)
    severity: Severity
    high_severity: Severity


def evaluate(facts: ClientFacts, cfg: LateCommitmentConfig) -> list[SignalDraft]:
    drafts = []
    for item in sorted(facts.commitments, key=lambda c: str(c.id)):
        if (
            item.owner_side != "firm"
            or item.status not in {"open", "late"}
            or item.due_date is None
        ):
            continue
        due_at = datetime.combine(item.due_date, time.min, tzinfo=facts.ref.tzinfo)
        if not item.evidence_ids or not in_window(
            due_at, facts.ref, newer_than_days=cfg.window_days + 1, up_to_days=1
        ):
            continue
        days_late = (facts.ref.date() - item.due_date).days
        drafts.append(
            SignalDraft(
                family=cfg.family,
                type="late_commitment",
                severity=cfg.high_severity if days_late >= cfg.high_overdue_days else cfg.severity,
                occurred_at=due_at,
                basis="evidence",
                dedupe_key=str(item.id),
                evidence_ids=item.evidence_ids,
            )
        )
    return drafts


SPEC = RuleSpec(LateCommitmentConfig, evaluate)
