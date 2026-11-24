"""Count client-started threads against the earlier initiation rate."""

from pydantic import Field

from firra.signals.facts import ClientFacts, RecordFact
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window


class ClientInitiationDropConfig(RuleConfig):
    window_days: int = Field(gt=0)
    baseline_days: int = Field(gt=0)
    min_baseline_per_window: float = Field(gt=0)
    max_ratio: float = Field(gt=0)
    watch_ratio: float = Field(gt=0)
    severity: Severity
    watch_severity: Severity
    dedupe_key: str
    chat_burst_minutes: int = Field(default=30, gt=0)


def _chat_starts(facts: ClientFacts, burst_minutes: int) -> list[RecordFact]:
    """Client chat touches that are not a reply (no firm message right before in the channel)."""
    touches = {r.id for r in facts.client_touches(burst_minutes) if r.source == "chat"}
    previous: dict[str | None, RecordFact] = {}
    starts = []
    for record in sorted(facts.records, key=lambda r: (r.occurred_at, str(r.id))):
        if record.source != "chat" or record.occurred_at > facts.ref:
            continue
        before = previous.get(record.thread_key)
        previous[record.thread_key] = record
        if record.id in touches and (before is None or before.author_side != "firm"):
            starts.append(record)
    return starts


def evaluate(facts: ClientFacts, cfg: ClientInitiationDropConfig) -> list[SignalDraft]:
    first: dict[str, RecordFact] = {}
    for record in sorted(facts.records, key=lambda r: (r.occurred_at, str(r.id))):
        if (
            record.source == "email"
            and record.thread_key is not None
            and record.occurred_at <= facts.ref
        ):
            first.setdefault(record.thread_key, record)
    starts = [r for r in first.values() if r.direction == "in" and r.author_side == "client"]
    starts += _chat_starts(facts, cfg.chat_burst_minutes)
    recent = sum(
        in_window(r.occurred_at, facts.ref, newer_than_days=cfg.window_days) for r in starts
    )
    older = sum(
        in_window(
            r.occurred_at, facts.ref, newer_than_days=cfg.baseline_days, up_to_days=cfg.window_days
        )
        for r in starts
    )
    baseline = older / ((cfg.baseline_days - cfg.window_days) / cfg.window_days)
    if baseline < cfg.min_baseline_per_window or recent > cfg.watch_ratio * baseline:
        return []
    return [
        SignalDraft(
            type="client_initiation_drop",
            family=cfg.family,
            severity=cfg.severity if recent <= cfg.max_ratio * baseline else cfg.watch_severity,
            occurred_at=facts.ref,
            basis="metadata",
            dedupe_key=cfg.dedupe_key,
            inputs={"initiated_30": recent, "baseline_per_30": round(baseline, 2)},
        )
    ]


SPEC = RuleSpec(ClientInitiationDropConfig, evaluate, uses_volume=True)
