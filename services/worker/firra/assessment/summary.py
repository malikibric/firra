"""Churn Replay summary (P2b D8): a pure function over one replay run's weekly states.

Detection is the first step at ``elevated`` or ``high`` whose next step is too (one noisy week is
not a warning). Lead days count back from the churn's notice date when the firm recorded one, else
from the churn date. The summary holds client ids, dates and numbers only.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from statistics import median
from typing import Any
from uuid import UUID

from firra.assessment.config import RiskState, risk_rank

ELEVATED = risk_rank("elevated")
DETECTED_AHEAD_DAYS = 30


@dataclass(frozen=True, slots=True)
class Step:
    as_of: date
    risk: RiskState
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClientSeries:
    client_id: UUID
    status: str  # active | churned
    steps: Sequence[Step]
    churned_at: date | None = None
    notice_at: date | None = None
    first_known_at: date | None = None


@dataclass(frozen=True, slots=True)
class ClientSummary:
    client_id: UUID
    status: str
    churned_at: date | None
    notice_at: date | None
    first_known_at: date | None
    detection_at: date | None
    lead_days: int | None
    max_state: RiskState | None
    weeks_elevated: int
    flicker: int
    miss_reason: str | None

    def as_json(self) -> dict[str, Any]:
        def iso(d: date | None) -> str | None:
            return d.isoformat() if d else None

        return {
            "client_id": str(self.client_id),
            "status": self.status,
            "churned_at": iso(self.churned_at),
            "notice_at": iso(self.notice_at),
            "first_known_at": iso(self.first_known_at),
            "detection_at": iso(self.detection_at),
            "lead_days": self.lead_days,
            "max_state": self.max_state,
            "weeks_elevated": self.weeks_elevated,
            "flicker": self.flicker,
            "miss_reason": self.miss_reason,
        }


@dataclass(frozen=True, slots=True)
class RunSummary:
    clients: list[ClientSummary]
    totals: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {"clients": [c.as_json() for c in self.clients], "totals": self.totals}


def detection(steps: Sequence[Step]) -> date | None:
    for current, following in pairwise(steps):
        if risk_rank(current.risk) >= ELEVATED and risk_rank(following.risk) >= ELEVATED:
            return current.as_of
    return None


def flicker(steps: Sequence[Step]) -> int:
    """Up, down, up within four consecutive steps."""
    ranks = [risk_rank(s.risk) for s in steps]
    return sum(
        ranks[i + 1] > ranks[i] and ranks[i + 2] < ranks[i + 1] and ranks[i + 3] > ranks[i + 2]
        for i in range(len(ranks) - 3)
    )


def _miss_reason(series: ClientSeries, detected: date | None, lead: int | None) -> str | None:
    if series.status != "churned":
        return None
    if detected is not None and lead is not None:
        return None if lead >= DETECTED_AHEAD_DAYS else "late"
    if any("coverage_changed" in s.caveats for s in series.steps):
        return "coverage_changed"
    if series.steps and "low_data" in series.steps[-1].caveats:
        return "low_data"
    return "no_signal"


def _max_state(steps: Sequence[Step]) -> RiskState | None:
    states = [s.risk for s in steps]
    if not states:
        return None
    best: RiskState = max(states, key=risk_rank)
    return best


def summarize_client(series: ClientSeries, step_days: int) -> ClientSummary:
    steps = sorted(series.steps, key=lambda s: s.as_of)
    detected = detection(steps)
    end = series.notice_at or series.churned_at
    lead = (end - detected).days if detected and end and series.status == "churned" else None
    return ClientSummary(
        client_id=series.client_id,
        status=series.status,
        churned_at=series.churned_at,
        notice_at=series.notice_at,
        first_known_at=series.first_known_at,
        detection_at=detected,
        lead_days=lead,
        max_state=_max_state(steps),
        weeks_elevated=round(sum(risk_rank(s.risk) >= ELEVATED for s in steps) * step_days / 7),
        flicker=flicker(steps),
        miss_reason=_miss_reason(series, detected, lead),
    )


def summarize(
    series: Sequence[ClientSeries], *, window_start: date, window_end: date, step_days: int
) -> RunSummary:
    clients = sorted(
        (summarize_client(s, step_days) for s in series), key=lambda c: str(c.client_id)
    )
    losses = [c for c in clients if c.status == "churned"]
    retained = [c for c in clients if c.status != "churned"]
    leads = [c.lead_days for c in losses if c.lead_days is not None]
    months = max((window_end - window_start).days, 1) / (365.25 / 12)
    alarm_weeks = sum(c.weeks_elevated for c in retained)
    per_10_per_month = round(alarm_weeks / (len(retained) / 10) / months, 3) if retained else 0.0
    totals: dict[str, Any] = {
        "losses": len(losses),
        "losses_detected_30d": sum(
            c.lead_days is not None and c.lead_days >= DETECTED_AHEAD_DAYS for c in losses
        ),
        "median_lead_days": median(leads) if leads else None,
        "retained": len(retained),
        "retained_with_elevated": sum(c.weeks_elevated > 0 for c in retained),
        "false_alarm_client_weeks_per_10_clients_per_month": per_10_per_month,
        "flicker": sum(c.flicker for c in clients),
    }
    return RunSummary(clients=clients, totals=totals)
