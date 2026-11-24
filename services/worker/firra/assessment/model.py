"""What an assessment is, as plain immutable data (no I/O)."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

from firra.assessment.config import ExpansionState, Play, RiskState
from firra.signals.model import Basis, Family, Severity


@dataclass(frozen=True, slots=True)
class Caveat:
    """A reason to read the assessment carefully; ``data`` holds ids and numbers only."""

    code: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, **self.data}


@dataclass(frozen=True, slots=True)
class Driver:
    type: str
    family: Family | None
    severity: Severity
    # The severity that counted for the risk state (a holiday caps volume drivers at low).
    counted_severity: Severity
    basis: Basis
    dedupe_key: str
    occurred_at: datetime
    inputs: dict[str, Any] | None
    evidence_ids: tuple[UUID, ...]
    primary: bool

    def as_json(self, signal_id: UUID | None = None) -> dict[str, Any]:
        return {
            "type": self.type,
            "family": self.family,
            "severity": self.severity,
            "counted_severity": self.counted_severity,
            "basis": self.basis,
            "dedupe_key": self.dedupe_key,
            "signal_id": str(signal_id) if signal_id else None,
            "occurred_at": self.occurred_at.isoformat(),
            "inputs": self.inputs,
            "evidence_ids": [str(e) for e in self.evidence_ids],
            "primary": self.primary,
        }


@dataclass(frozen=True, slots=True)
class Assessment:
    as_of: date
    risk_state: RiskState
    previous_risk_state: RiskState | None
    state_since: date
    expansion_state: ExpansionState
    strength: str
    drivers: tuple[Driver, ...]
    caveats: tuple[Caveat, ...]
    play: Play
    # Deterministic working data: family levels, the computed (pre-hysteresis) state, the number
    # of consecutive runs below the current state, and counter-evidence ids.
    data: dict[str, Any]

    @property
    def top_driver(self) -> Driver | None:
        return next((d for d in self.drivers if d.primary), None)

    def evidence_ids(self) -> tuple[UUID, ...]:
        ids: dict[UUID, None] = {}
        for driver in self.drivers:
            ids.update(dict.fromkeys(driver.evidence_ids))
        return tuple(ids)


@dataclass(frozen=True, slots=True)
class PreviousAssessment:
    """What hysteresis needs from the series' latest assessment before this one."""

    as_of: date
    risk_state: RiskState
    state_since: date
    below_runs: int
