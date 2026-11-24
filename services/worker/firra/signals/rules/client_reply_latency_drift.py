"""Compare immediately preceding firm-to-client reply gaps across the account, per source.

Email gaps are wall-clock hours (unchanged since signals.v2); chat gaps are working hours in the
workspace time zone with their own thresholds, and fire their own signal (``latency:chat``).
"""

from statistics import median

from pydantic import BaseModel, ConfigDict, Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec
from firra.signals.rules.reply_gaps import replies


class ChatLatency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_recent_replies: int = Field(gt=0)
    min_baseline_replies: int = Field(gt=0)
    min_recent_median_wh: float = Field(gt=0)
    watch_median_wh: float = Field(gt=0)
    min_ratio: float = Field(gt=0)
    watch_ratio: float = Field(gt=0)


class ClientReplyLatencyDriftConfig(RuleConfig):
    window_days: int = Field(gt=0)
    baseline_days: int = Field(gt=0)
    min_recent_replies: int = Field(gt=0)
    min_baseline_replies: int = Field(gt=0)
    min_recent_median_h: float = Field(gt=0)
    min_ratio: float = Field(gt=0)
    watch_median_h: float = Field(gt=0)
    watch_ratio: float = Field(gt=0)
    severity: Severity
    watch_severity: Severity
    dedupe_key: str
    chat: ChatLatency | None = None


def evaluate(facts: ClientFacts, cfg: ClientReplyLatencyDriftConfig) -> list[SignalDraft]:
    drafts = _source(
        facts,
        cfg,
        source="email",
        thresholds=(
            cfg.min_recent_replies,
            cfg.min_baseline_replies,
            cfg.min_recent_median_h,
            cfg.watch_median_h,
            cfg.min_ratio,
            cfg.watch_ratio,
        ),
    )
    if cfg.chat is not None:
        chat = cfg.chat
        drafts += _source(
            facts,
            cfg,
            source="chat",
            thresholds=(
                chat.min_recent_replies,
                chat.min_baseline_replies,
                chat.min_recent_median_wh,
                chat.watch_median_wh,
                chat.min_ratio,
                chat.watch_ratio,
            ),
        )
    return drafts


def _source(
    facts: ClientFacts,
    cfg: ClientReplyLatencyDriftConfig,
    *,
    source: str,
    thresholds: tuple[int, int, float, float, float, float],
) -> list[SignalDraft]:
    min_recent, min_baseline, min_median, watch_median, min_ratio, watch_ratio = thresholds
    found = replies(
        facts,
        "chat" if source == "chat" else "email",
        window_days=cfg.window_days,
        baseline_days=cfg.baseline_days,
    )
    recent = [r for r in found if r.recent]
    baseline = [r.gap for r in found if r.baseline]
    if len(recent) < min_recent or len(baseline) < min_baseline:
        return []
    current = float(median(r.gap for r in recent))
    base = float(median(baseline))
    if current < watch_median or current < watch_ratio * base:
        return []
    severity = (
        cfg.severity
        if current >= min_median and current >= min_ratio * base
        else cfg.watch_severity
    )
    unit = "wh" if source == "chat" else "h"
    inputs: dict[str, object] = {
        f"recent_median_{unit}": round(current, 2),
        f"base_median_{unit}": round(base, 2),
        "recent_n": len(recent),
        "base_n": len(baseline),
    }
    if source == "chat":
        inputs["source"] = "chat"
    return [
        SignalDraft(
            type="client_reply_latency_drift",
            family=cfg.family,
            severity=severity,
            occurred_at=max(r.at for r in recent),
            basis="metadata",
            dedupe_key=f"{cfg.dedupe_key}:chat" if source == "chat" else cfg.dedupe_key,
            inputs=inputs,
        )
    ]


SPEC = RuleSpec(ClientReplyLatencyDriftConfig, evaluate, uses_volume=True)
