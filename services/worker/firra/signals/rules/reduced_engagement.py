"""reduced_engagement (F3): the client writes far less than it used to. Metadata only, so it also
covers ``metadata_only`` clients."""

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window


class ReducedEngagementConfig(RuleConfig):
    window_days: int
    baseline_days: int
    min_baseline_per_window: float
    watch_ratio: float
    max_ratio: float
    chat_burst_minutes: int = Field(default=30, gt=0)


def evaluate(facts: ClientFacts, cfg: ReducedEngagementConfig) -> list[SignalDraft]:
    messages = facts.client_touches(cfg.chat_burst_minutes)
    recent = sum(
        in_window(r.occurred_at, facts.ref, newer_than_days=cfg.window_days) for r in messages
    )
    older = sum(
        in_window(
            r.occurred_at,
            facts.ref,
            newer_than_days=cfg.baseline_days,
            up_to_days=cfg.window_days,
        )
        for r in messages
    )
    windows = (cfg.baseline_days - cfg.window_days) / cfg.window_days
    baseline = older / windows
    if baseline < cfg.min_baseline_per_window or recent > cfg.watch_ratio * baseline:
        return []
    return [
        SignalDraft(
            family=cfg.family,
            type="reduced_engagement",
            severity="high"
            if recent == 0
            else "medium"
            if recent <= cfg.max_ratio * baseline
            else "low",
            occurred_at=facts.ref,
            basis="metadata",
            dedupe_key="volume",
            inputs={"messages_30": recent, "baseline_per_30": round(baseline, 2)},
        )
    ]


SPEC = RuleSpec(ReducedEngagementConfig, evaluate, uses_volume=True)
