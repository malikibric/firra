"""A renewal or its notice deadline is approaching."""

from datetime import datetime, time, timedelta

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window


class RenewalWindowConfig(RuleConfig):
    renewal_window_days: int = Field(gt=0)
    notice_window_days: int = Field(gt=0)
    high_notice_days: int = Field(ge=0)
    severity: Severity
    high_severity: Severity


def evaluate(facts: ClientFacts, cfg: RenewalWindowConfig) -> list[SignalDraft]:
    if facts.renewal_date is None:
        return []
    renewal_at = datetime.combine(facts.renewal_date, time.min, tzinfo=facts.ref.tzinfo)
    notice_date = (
        facts.renewal_date - timedelta(days=facts.notice_period_days)
        if facts.notice_period_days is not None
        else None
    )
    notice_at = (
        datetime.combine(notice_date, time.min, tzinfo=facts.ref.tzinfo)
        if notice_date is not None
        else None
    )
    renewal_near = in_window(
        renewal_at,
        facts.ref + timedelta(days=cfg.renewal_window_days),
        newer_than_days=cfg.renewal_window_days,
    )
    notice_near = notice_at is not None and in_window(
        notice_at,
        facts.ref + timedelta(days=cfg.notice_window_days),
        newer_than_days=cfg.notice_window_days,
    )
    if not (renewal_near or notice_near):
        return []
    days_to_notice = (notice_date - facts.ref.date()).days if notice_date is not None else None
    return [
        SignalDraft(
            family=cfg.family,
            type="renewal_window",
            severity=(
                cfg.high_severity
                if days_to_notice is not None and days_to_notice <= cfg.high_notice_days
                else cfg.severity
            ),
            occurred_at=facts.ref,
            basis="metadata",
            dedupe_key=facts.renewal_date.isoformat(),
            inputs={
                "renewal_date": facts.renewal_date.isoformat(),
                "notice_deadline": notice_date.isoformat() if notice_date else None,
                "days_to_notice": days_to_notice,
            },
        )
    ]


SPEC = RuleSpec(RenewalWindowConfig, evaluate)
