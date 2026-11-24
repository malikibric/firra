"""Recent verified client consideration of an in-house or competitor alternative."""

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, evidence_ids, in_window, newest


class InHouseRiskConfig(RuleConfig):
    window_days: int = Field(gt=0)
    max_evidence: int = Field(gt=0)
    severity: Severity
    dedupe_key: str


def evaluate(facts: ClientFacts, cfg: InHouseRiskConfig) -> list[SignalDraft]:
    items = [
        item
        for item in facts.extractions
        if item.kind == "in_house_or_competitor_mention"
        and item.evidence_ids
        and in_window(item.occurred_at, facts.ref, newer_than_days=cfg.window_days)
    ]
    if not items:
        return []
    return [
        SignalDraft(
            family=cfg.family,
            type="in_house_risk",
            severity=cfg.severity,
            occurred_at=newest(items)[0].occurred_at,
            basis="evidence",
            dedupe_key=cfg.dedupe_key,
            evidence_ids=evidence_ids(items, cfg.max_evidence),
        )
    ]


SPEC = RuleSpec(InHouseRiskConfig, evaluate)
