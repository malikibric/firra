"""The recommended play (P2b D9): deterministic, first matching rule wins."""

from collections.abc import Sequence

from firra.assessment.config import (
    ExpansionState,
    Play,
    PlayRule,
    PlaysConfig,
    RiskState,
    risk_rank,
)
from firra.assessment.model import Driver
from firra.signals.model import SignalDraft


def days_to_notice(drafts: Sequence[SignalDraft]) -> int | None:
    """The nearest notice deadline from `renewal_window` drafts, in days (None without one)."""
    days = [
        int(d.inputs["days_to_notice"])
        for d in drafts
        if d.type == "renewal_window" and d.inputs and d.inputs.get("days_to_notice") is not None
    ]
    return min(days) if days else None


def _matches(
    rule: PlayRule,
    *,
    risk: RiskState,
    expansion: ExpansionState,
    top: Driver | None,
    notice_days: int | None,
    unresolved_within: dict[int, bool],
) -> bool:
    if rule.min_risk is not None and risk_rank(risk) < risk_rank(rule.min_risk):
        return False
    if rule.max_risk is not None and risk_rank(risk) > risk_rank(rule.max_risk):
        return False
    if rule.expansion is not None and expansion != rule.expansion:
        return False
    if rule.renewal_within_days is not None and (
        notice_days is None or notice_days > rule.renewal_within_days
    ):
        return False
    family_ok = rule.top_driver_families is None or (
        top is not None and top.family in rule.top_driver_families
    )
    if rule.unresolved_complaint_within_days is not None:
        return family_ok or unresolved_within[rule.unresolved_complaint_within_days]
    return family_ok


def choose_play(
    *,
    risk: RiskState,
    expansion: ExpansionState,
    top: Driver | None,
    drafts: Sequence[SignalDraft],
    unresolved_within: dict[int, bool],
    cfg: PlaysConfig,
) -> Play:
    """``unresolved_within`` maps each configured window (days) to "an unresolved complaint
    exists within it"."""
    notice_days = days_to_notice(drafts)
    for rule in cfg.plays:
        if _matches(
            rule,
            risk=risk,
            expansion=expansion,
            top=top,
            notice_days=notice_days,
            unresolved_within=unresolved_within,
        ):
            return rule.play
    return "monitor"  # pragma: no cover - the config always ends with monitor


def complaint_windows(cfg: PlaysConfig) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                r.unresolved_complaint_within_days
                for r in cfg.plays
                if r.unresolved_complaint_within_days is not None
            }
        )
    )
