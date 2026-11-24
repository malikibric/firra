"""Frozen routine inputs and outputs from the milestone E engine contract."""

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from firra.assessment.config import ExpansionState, RiskState

Trigger = Literal[
    "risk_up",
    "risk_down",
    "expansion_up",
    "signal_opened",
    "signal_resolved",
    "commitment_late",
    "notice_close",
]
Importance = Literal["fyi", "attention", "important"]
Kind = Literal["state_change", "commitment_watch", "expansion"]


@dataclass(frozen=True, slots=True)
class Candidate:
    client_id: UUID
    kind: Kind
    trigger: Trigger
    as_of: date
    risk_to: RiskState | None = None
    expansion_to: ExpansionState | None = None
    severity: Literal["low", "medium", "high"] | None = None
    owner_side: Literal["firm", "client"] | None = None
    evidence_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class ClientContext:
    client_id: UUID
    policy: Literal["full", "metadata_only"]
    risk: RiskState
    days_to_notice: int | None


@dataclass(frozen=True, slots=True)
class Prior:
    kind: Kind
    reason_code: str
    verdict: Literal["useful", "already_known", "wrong", "not_now"] | None
    surfaced_on: date
    evidence_ids: frozenset[UUID]
    risk_rank: int


@dataclass(frozen=True, slots=True)
class Decision:
    client_id: UUID
    kind: Kind
    reason_code: str
    importance: Importance
    demoted_reason: Literal["budget"] | None
    also: tuple[str, ...]
    evidence_ids: frozenset[UUID]
    risk_rank: int
    days_to_notice: int | None
    as_of: date


@dataclass(frozen=True, slots=True)
class Plan:
    decisions: tuple[Decision, ...]
    counts: dict[str, int]
