"""Held meetings have fewer attendees than the earlier held meetings."""

from statistics import mean

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window


class MeetingAttendanceDropConfig(RuleConfig):
    window_days: int = Field(gt=0)
    baseline_days: int = Field(gt=0)
    min_recent_events: int = Field(gt=0)
    min_baseline_events: int = Field(gt=0)
    max_ratio: float = Field(gt=0)
    severity: Severity
    dedupe_key: str


def evaluate(facts: ClientFacts, cfg: MeetingAttendanceDropConfig) -> list[SignalDraft]:
    held = [e for e in facts.events if e.status == "held" and e.attendee_count is not None]
    recent = [e for e in held if in_window(e.starts_at, facts.ref, newer_than_days=cfg.window_days)]
    older = [
        e
        for e in held
        if in_window(
            e.starts_at, facts.ref, newer_than_days=cfg.baseline_days, up_to_days=cfg.window_days
        )
    ]
    if len(recent) < cfg.min_recent_events or len(older) < cfg.min_baseline_events:
        return []
    current = mean(e.attendee_count for e in recent if e.attendee_count is not None)
    baseline = mean(e.attendee_count for e in older if e.attendee_count is not None)
    if current > cfg.max_ratio * baseline:
        return []
    return [
        SignalDraft(
            type="meeting_attendance_drop",
            family=cfg.family,
            severity=cfg.severity,
            occurred_at=max(e.starts_at for e in recent),
            basis="metadata",
            dedupe_key=cfg.dedupe_key,
            inputs={
                "mean_90": round(current, 2),
                "base_mean": round(baseline, 2),
                "recent_n": len(recent),
                "base_n": len(older),
            },
        )
    ]


SPEC = RuleSpec(MeetingAttendanceDropConfig, evaluate)
