"""Pure, deterministic learning math over content-free rows."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Any
from uuid import UUID

from firra.assessment.config import RiskState


@dataclass(frozen=True)
class FeedbackRow:
    card_id: UUID
    client_id: UUID
    kind: str
    play: str
    verdict: str
    action: str | None


@dataclass(frozen=True)
class AssessmentRow:
    client_id: UUID
    as_of: date
    risk_state: RiskState
    primary_families: tuple[str, ...]


@dataclass(frozen=True)
class OutcomeRow:
    client_id: UUID
    event: str
    occurred_at: date
    notice_at: date | None


@dataclass(frozen=True)
class JsonResult:
    def as_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UsefulnessGroup(JsonResult):
    dimension: str
    value: str
    n: int
    useful: int
    already_known: int
    wrong: int
    not_now: int
    rated: int
    acted: int
    useful_rate: float | None
    wrong_rate: float | None


@dataclass(frozen=True)
class Episode:
    client_id: UUID
    start: date
    families: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "client_id": str(self.client_id),
            "start": self.start.isoformat(),
            "families": list(self.families),
        }


@dataclass(frozen=True)
class FamilyPrecision(JsonResult):
    family: str
    confirmed: int
    not_confirmed: int
    open: int
    precision: float | None


@dataclass(frozen=True)
class FamilyLead(JsonResult):
    family: str
    detected: int
    median_lead_days: int | None
    share_ge_30d: float | None


@dataclass(frozen=True)
class LeadSummary(JsonResult):
    detected: int
    missed: int
    recall: float | None
    median_lead_days: int | None
    share_ge_30d: float | None
    families: tuple[FamilyLead, ...]


def usefulness(rows: Sequence[FeedbackRow], min_rated: int) -> list[UsefulnessGroup]:
    groups: dict[tuple[str, str], list[FeedbackRow]] = defaultdict(list)
    for row in rows:
        groups["kind", row.kind].append(row)
        groups["play", row.play].append(row)
    result = []
    for (dimension, value), group in sorted(groups.items()):
        counts = [
            sum(r.verdict == v for r in group)
            for v in ("useful", "already_known", "wrong", "not_now")
        ]
        useful, known, wrong, later = counts
        rated = useful + known + wrong
        acted = sum(r.verdict == "useful" and r.action in ("taken", "planned") for r in group)
        result.append(
            UsefulnessGroup(
                dimension,
                value,
                len(group),
                useful,
                known,
                wrong,
                later,
                rated,
                acted,
                useful / rated if rated >= min_rated else None,
                wrong / rated if rated >= min_rated else None,
            )
        )
    return result


def episodes(assessments: Sequence[AssessmentRow]) -> list[Episode]:
    clients: dict[UUID, list[AssessmentRow]] = defaultdict(list)
    for row in assessments:
        clients[row.client_id].append(row)
    result = []
    for client_id, rows in sorted(clients.items(), key=lambda item: str(item[0])):
        active = False
        for current, following in pairwise(sorted(rows, key=lambda r: r.as_of)):
            elevated = current.risk_state in ("elevated", "high")
            if not elevated:
                active = False
            elif not active and following.risk_state in ("elevated", "high"):
                result.append(
                    Episode(client_id, current.as_of, tuple(sorted(set(current.primary_families))))
                )
                active = True
    return result


def precision(
    alerts: Sequence[Episode],
    outcomes: Sequence[OutcomeRow],
    as_of: date,
    horizon_days: int,
    min_rated: int,
) -> list[FamilyPrecision]:
    counts: dict[str, list[int]] = {"all": [0, 0, 0]}
    for alert in alerts:
        if alert.start > as_of:
            continue
        end = alert.start + timedelta(days=horizon_days)
        events = {
            o.event
            for o in outcomes
            if o.client_id == alert.client_id and alert.start < o.occurred_at <= min(end, as_of)
        }
        bucket = (
            0
            if events & {"churned", "downgrade"}
            else 1
            if events & {"renewed", "expanded"} or end <= as_of
            else 2
        )
        for family in {"all", *alert.families}:
            counts.setdefault(family, [0, 0, 0])[bucket] += 1
    return [
        FamilyPrecision(f, yes, no, pending, yes / (yes + no) if yes + no >= min_rated else None)
        for f, (yes, no, pending) in sorted(counts.items())
    ]


def _lead_values(days: Sequence[int]) -> tuple[int | None, float | None]:
    if not days:
        return None, None
    return sorted(days)[(len(days) - 1) // 2], sum(d >= 30 for d in days) / len(days)


def lead_time(alerts: Sequence[Episode], outcomes: Sequence[OutcomeRow]) -> LeadSummary:
    """Score each recorded loss independently, using notice rather than hindsight when available."""
    leads: list[int] = []
    families: dict[str, list[int]] = defaultdict(list)
    missed = 0
    for outcome in outcomes:
        if outcome.event not in ("churned", "downgrade"):
            continue
        end = outcome.notice_at or outcome.occurred_at
        eligible = [
            e
            for e in alerts
            if e.client_id == outcome.client_id and end - timedelta(days=365) <= e.start < end
        ]
        if not eligible:
            missed += 1
            continue
        episode = min(eligible, key=lambda e: (e.start, tuple(sorted(set(e.families)))))
        days = (end - episode.start).days
        leads.append(days)
        for family in set(episode.families):
            families[family].append(days)
    middle, share = _lead_values(leads)
    return LeadSummary(
        len(leads),
        missed,
        len(leads) / (len(leads) + missed) if leads or missed else None,
        middle,
        share,
        tuple(FamilyLead(f, len(ds), *_lead_values(ds)) for f, ds in sorted(families.items())),
    )
