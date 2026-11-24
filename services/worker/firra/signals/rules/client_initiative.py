"""Client-authored client initiative with verified evidence."""

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, evidence_ids, in_window, newest


class ClientInitiativeConfig(RuleConfig):
    window_days: int = Field(gt=0)
    max_evidence: int = Field(gt=0)
    severity: Severity
    dedupe_key: str


def evaluate(facts: ClientFacts, cfg: ClientInitiativeConfig) -> list[SignalDraft]:
    client_ids = {r.id for r in facts.client_messages()}
    items = [
        item
        for item in facts.extractions
        if item.kind == "initiative"
        and item.record_id in client_ids
        and item.evidence_ids
        and in_window(item.occurred_at, facts.ref, newer_than_days=cfg.window_days)
    ]
    if not items:
        return []
    return [
        SignalDraft(
            type="client_initiative",
            family=cfg.family,
            severity=cfg.severity,
            occurred_at=newest(items)[0].occurred_at,
            basis="evidence",
            dedupe_key=cfg.dedupe_key,
            evidence_ids=evidence_ids(items, cfg.max_evidence),
        )
    ]


SPEC = RuleSpec(ClientInitiativeConfig, evaluate)
