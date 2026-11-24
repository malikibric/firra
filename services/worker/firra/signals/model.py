"""What a rule produces, before anything is stored."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

Severity = Literal["low", "medium", "high"]
Basis = Literal["evidence", "metadata"]
Family = Literal[
    "delivery",
    "sentiment",
    "engagement",
    "meetings",
    "stakeholder",
    "commercial",
    "timing",
    "expansion",
]

# Signals that always rest on a verified quote (mirrors the CHECK on public.signal).
EVIDENCE_ONLY = frozenset({"tone_decline", "executive_escalation", "delivery_complaint"})


@dataclass(frozen=True, slots=True)
class SignalDraft:
    type: str
    severity: Severity
    occurred_at: datetime
    basis: Basis
    dedupe_key: str
    family: Family | None = None
    evidence_ids: tuple[UUID, ...] = ()
    # Numeric inputs; required for metadata signals, optional context for evidence signals.
    inputs: dict[str, Any] | None = None
