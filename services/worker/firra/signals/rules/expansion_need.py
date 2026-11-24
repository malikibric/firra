"""An open recurring need matches a service offered by the workspace.

A need for a service the client already has is not an expansion: it is more of the same (often a
delivery topic), so it stays visible as a need but raises no expansion signal.
"""

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window


class ExpansionNeedConfig(RuleConfig):
    window_days: int = Field(gt=0)
    max_evidence: int = Field(gt=0)
    severity: Severity


def evaluate(facts: ClientFacts, cfg: ExpansionNeedConfig) -> list[SignalDraft]:
    return [
        SignalDraft(
            type="expansion_need",
            family=cfg.family,
            severity=cfg.severity,
            occurred_at=need.last_seen_at,
            basis="evidence",
            dedupe_key=str(need.id),
            evidence_ids=tuple(dict.fromkeys(need.evidence_ids))[: cfg.max_evidence],
        )
        for need in sorted(facts.needs, key=lambda n: str(n.id))
        if need.status == "open"
        and need.workspace_service_id is not None
        and not facts.buys(need.service_name)
        and need.evidence_ids
        and in_window(need.last_seen_at, facts.ref, newer_than_days=cfg.window_days)
    ]


SPEC = RuleSpec(ExpansionNeedConfig, evaluate)
