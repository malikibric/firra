"""An established onboarding period has no reported result yet."""

from datetime import datetime, time

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window


class First90DaysNoResultConfig(RuleConfig):
    min_days: int = Field(gt=0)
    max_days: int = Field(gt=0)
    severity: Severity
    dedupe_key: str


def evaluate(facts: ClientFacts, cfg: First90DaysNoResultConfig) -> list[SignalDraft]:
    if facts.contract_start is None:
        return []
    started = datetime.combine(facts.contract_start, time.min, tzinfo=facts.ref.tzinfo)
    if not in_window(started, facts.ref, newer_than_days=cfg.max_days + 1, up_to_days=cfg.min_days):
        return []
    if any(
        item.kind == "result_reported" and started <= item.occurred_at <= facts.ref
        for item in facts.extractions
    ):
        return []
    return [
        SignalDraft(
            family=cfg.family,
            type="first_90_days_no_result",
            severity=cfg.severity,
            occurred_at=facts.ref,
            basis="metadata",
            dedupe_key=cfg.dedupe_key,
            inputs={
                "contract_start": facts.contract_start.isoformat(),
                "days": (facts.ref.date() - facts.contract_start).days,
            },
        )
    ]


SPEC = RuleSpec(First90DaysNoResultConfig, evaluate)
