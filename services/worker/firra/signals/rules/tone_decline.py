"""tone_decline (F3): the share of negative-tone client messages rose against the account's own
baseline. Text of the client's messages, aggregated per account and always backed by the quotes;
never a score of a person (product rule 4)."""

from uuid import UUID

from pydantic import Field

from firra.signals.facts import ClientFacts, ExtractionFact
from firra.signals.model import SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window


class ToneDeclineConfig(RuleConfig):
    window_days: int
    baseline_days: int
    min_messages: int = Field(gt=0)
    min_share: float = Field(gt=0)
    min_increase: float
    high_share: float
    max_evidence: int


def _share(negative: int, total: int) -> float:
    return negative / total if total else 0.0


def evaluate(facts: ClientFacts, cfg: ToneDeclineConfig) -> list[SignalDraft]:
    processed = [r for r in facts.client_emails() if r.processed]
    recent = [
        r for r in processed if in_window(r.occurred_at, facts.ref, newer_than_days=cfg.window_days)
    ]
    if len(recent) < cfg.min_messages:
        return []
    baseline = [
        r
        for r in processed
        if in_window(
            r.occurred_at,
            facts.ref,
            newer_than_days=cfg.baseline_days,
            up_to_days=cfg.window_days,
        )
    ]
    negative_records: dict[UUID, ExtractionFact] = {
        e.record_id: e
        for e in facts.extractions
        if e.kind == "tone" and e.payload.get("tone") == "negative"
    }
    negative_recent = [r for r in recent if r.id in negative_records]
    share_recent = _share(len(negative_recent), len(recent))
    share_base = _share(sum(r.id in negative_records for r in baseline), len(baseline))
    # Rounded so 0.5 - 0.3 counts as the 0.2 it is.
    if (
        round(share_recent, 6) < cfg.min_share
        or round(share_recent - share_base, 6) < cfg.min_increase
    ):
        return []

    newest = sorted(negative_recent, key=lambda r: r.occurred_at, reverse=True)[: cfg.max_evidence]
    evidence = tuple(
        evidence_id for r in newest for evidence_id in negative_records[r.id].evidence_ids
    )
    return [
        SignalDraft(
            family=cfg.family,
            type="tone_decline",
            severity="high" if round(share_recent, 6) >= cfg.high_share else "medium",
            occurred_at=newest[0].occurred_at,
            basis="evidence",
            dedupe_key="tone",
            evidence_ids=evidence,
            inputs={
                "share_30": round(share_recent, 4),
                "share_base": round(share_base, 4),
                "messages_30": len(recent),
            },
        )
    ]


SPEC = RuleSpec(ToneDeclineConfig, evaluate)
