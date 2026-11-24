"""Recent client complaints, one signal per delivery subtype."""

from collections import defaultdict

from pydantic import Field

from firra.signals.facts import ClientFacts, ExtractionFact
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, evidence_ids, in_window, newest


class DeliveryComplaintConfig(RuleConfig):
    window_days: int = Field(gt=0)
    subtypes: tuple[str, ...]
    high_count: int = Field(gt=0)
    max_evidence: int = Field(gt=0)
    severity: Severity
    high_severity: Severity


def evaluate(facts: ClientFacts, cfg: DeliveryComplaintConfig) -> list[SignalDraft]:
    client_ids = {r.id for r in facts.client_messages()}
    grouped: dict[str, list[ExtractionFact]] = defaultdict(list)
    for item in facts.extractions:
        subtype = item.payload.get("subtype")
        if (
            item.kind == "complaint"
            and item.record_id in client_ids
            and subtype in cfg.subtypes
            and item.evidence_ids
            and in_window(item.occurred_at, facts.ref, newer_than_days=cfg.window_days)
        ):
            grouped[subtype].append(item)
    return [
        SignalDraft(
            family=cfg.family,
            type="delivery_complaint",
            severity=cfg.high_severity if len(items) >= cfg.high_count else cfg.severity,
            occurred_at=newest(items)[0].occurred_at,
            basis="evidence",
            dedupe_key=subtype,
            evidence_ids=evidence_ids(items, cfg.max_evidence),
        )
        for subtype, items in sorted(grouped.items())
    ]


SPEC = RuleSpec(DeliveryComplaintConfig, evaluate)
