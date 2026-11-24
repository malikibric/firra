"""Client-authored scope cut talk with verified evidence."""

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, evidence_ids, in_window, newest


class ScopeCutTalkConfig(RuleConfig):
    window_days: int = Field(gt=0)
    max_evidence: int = Field(gt=0)
    severity: Severity
    dedupe_key: str
    medium_records: int = Field(gt=0)
    medium_severity: Severity


def evaluate(facts: ClientFacts, cfg: ScopeCutTalkConfig) -> list[SignalDraft]:
    client_ids = {r.id for r in facts.client_messages()}
    items = [
        item
        for item in facts.extractions
        if item.kind == "renewal_or_budget_talk"
        and item.record_id in client_ids
        and item.payload.get("budget_direction") == "cut"
        and item.evidence_ids
        and in_window(item.occurred_at, facts.ref, newer_than_days=cfg.window_days)
    ]
    if not items:
        return []
    return [
        SignalDraft(
            type="scope_cut_talk",
            family=cfg.family,
            severity=cfg.medium_severity
            if len({item.record_id for item in items}) >= cfg.medium_records
            else cfg.severity,
            occurred_at=newest(items)[0].occurred_at,
            basis="evidence",
            dedupe_key=cfg.dedupe_key,
            evidence_ids=evidence_ids(items, cfg.max_evidence),
        )
    ]


SPEC = RuleSpec(ScopeCutTalkConfig, evaluate)
