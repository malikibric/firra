"""The champion's replies have slowed against their own earlier replies, per source.

Email in wall-clock hours (unchanged), chat in working hours with its own thresholds (dedupe key
``<person id>:chat``).
"""

from statistics import median
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec
from firra.signals.rules.reply_gaps import replies


class ChatChampionLatency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_recent_replies: int = Field(gt=0)
    min_baseline_replies: int = Field(gt=0)
    min_recent_median_wh: float = Field(gt=0)
    min_ratio: float = Field(gt=0)


class ChampionLatencyDriftConfig(RuleConfig):
    window_days: int = Field(gt=0)
    baseline_days: int = Field(gt=0)
    min_recent_replies: int = Field(gt=0)
    min_baseline_replies: int = Field(gt=0)
    min_recent_median_h: float = Field(gt=0)
    min_ratio: float = Field(gt=0)
    severity: Severity
    chat: ChatChampionLatency | None = None


def evaluate(facts: ClientFacts, cfg: ChampionLatencyDriftConfig) -> list[SignalDraft]:
    champions = sorted((p.id for p in facts.people if p.is_champion), key=str)
    drafts = _source(
        facts,
        cfg,
        champions,
        "email",
        (cfg.min_recent_replies, cfg.min_baseline_replies, cfg.min_recent_median_h, cfg.min_ratio),
    )
    if cfg.chat is not None:
        chat = cfg.chat
        drafts += _source(
            facts,
            cfg,
            champions,
            "chat",
            (
                chat.min_recent_replies,
                chat.min_baseline_replies,
                chat.min_recent_median_wh,
                chat.min_ratio,
            ),
        )
    return drafts


def _source(
    facts: ClientFacts,
    cfg: ChampionLatencyDriftConfig,
    champions: list[UUID],
    source: str,
    thresholds: tuple[int, int, float, float],
) -> list[SignalDraft]:
    min_recent, min_baseline, min_median, min_ratio = thresholds
    found = replies(
        facts,
        "chat" if source == "chat" else "email",
        window_days=cfg.window_days,
        baseline_days=cfg.baseline_days,
    )
    unit = "wh" if source == "chat" else "h"
    drafts = []
    for person_id in champions:
        mine = [r for r in found if r.author == person_id]
        current = [r for r in mine if r.recent]
        older = [r.gap for r in mine if r.baseline]
        if len(current) < min_recent or len(older) < min_baseline:
            continue
        recent_median = float(median(r.gap for r in current))
        base_median = float(median(older))
        if recent_median < min_median or recent_median < min_ratio * base_median:
            continue
        inputs: dict[str, object] = {
            f"recent_median_{unit}": round(recent_median, 2),
            f"base_median_{unit}": round(base_median, 2),
            "recent_n": len(current),
            "base_n": len(older),
        }
        if source == "chat":
            inputs["source"] = "chat"
        drafts.append(
            SignalDraft(
                family=cfg.family,
                type="champion_latency_drift",
                severity=cfg.severity,
                occurred_at=max(r.at for r in current),
                basis="metadata",
                dedupe_key=f"{person_id}:chat" if source == "chat" else str(person_id),
                inputs=inputs,
            )
        )
    return drafts


SPEC = RuleSpec(ChampionLatencyDriftConfig, evaluate, uses_volume=True)
