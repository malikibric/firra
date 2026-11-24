"""Assessment and play configuration (config/assessment.yaml, config/plays.yaml; P2b D6, D9)."""

from functools import cache
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from firra.signals.config import config_dir
from firra.signals.model import Family

RiskState = Literal["stable", "watch", "elevated", "high"]
ExpansionState = Literal["none", "hypothesis", "strong"]
Play = Literal[
    "fix_first",
    "stakeholder_remap",
    "renewal_prep",
    "founder_review_call",
    "expansion_conversation",
    "monitor",
]

RISK_ORDER: tuple[RiskState, ...] = ("stable", "watch", "elevated", "high")


def risk_rank(state: RiskState) -> int:
    return RISK_ORDER.index(state)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimingConfig(_Frozen):
    escalate_within_days: int = Field(gt=0)


class CoverageConfig(_Frozen):
    tolerance_days: int = Field(ge=0)


class ExpansionConfig(_Frozen):
    inputs: tuple[str, ...]
    anchors: tuple[str, ...]
    strong_need_mentions: int = Field(gt=0)


class HolidayPeriod(_Frozen):
    start: str = Field(pattern=r"^\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{2}-\d{2}$")


class CaveatConfig(_Frozen):
    low_data_min_client_messages: int = Field(gt=0)
    low_data_window_days: int = Field(gt=0)
    holiday_window_days: int = Field(gt=0)
    holiday_periods: tuple[HolidayPeriod, ...]
    recent_positive_days: int = Field(gt=0)
    stakeholder_handover_days: int = Field(gt=0)
    handover_types: tuple[str, ...]


class AssessmentConfig(_Frozen):
    version: str
    risk_families: tuple[Family, ...]
    family_priority: tuple[Family, ...]
    primary_drivers: int = Field(gt=0)
    timing: TimingConfig
    deescalate_after: int = Field(gt=0)
    coverage: CoverageConfig
    expansion: ExpansionConfig
    caveats: CaveatConfig

    @model_validator(mode="after")
    def _priority_covers_risk(self) -> "AssessmentConfig":
        missing = set(self.risk_families) - set(self.family_priority)
        if missing:
            raise ValueError(f"family_priority misses {sorted(missing)}")
        return self


class PlayRule(_Frozen):
    play: Play
    min_risk: RiskState | None = None
    max_risk: RiskState | None = None
    top_driver_families: tuple[Family, ...] | None = None
    unresolved_complaint_within_days: int | None = Field(default=None, gt=0)
    renewal_within_days: int | None = Field(default=None, gt=0)
    expansion: ExpansionState | None = None


class PlaysConfig(_Frozen):
    version: str
    plays: tuple[PlayRule, ...]

    @field_validator("plays")
    @classmethod
    def _ends_with_monitor(cls, plays: tuple[PlayRule, ...]) -> tuple[PlayRule, ...]:
        last = plays[-1] if plays else None
        if (
            last is None
            or last.play != "monitor"
            or last.model_dump(exclude_none=True) != {"play": "monitor"}
        ):
            raise ValueError("the last play must be an unconditional monitor")
        return plays


def _read(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((config_dir() / name).read_text())
    return data


@cache
def load_assessment_config() -> AssessmentConfig:
    return AssessmentConfig.model_validate(_read("assessment.yaml"))


@cache
def load_plays_config() -> PlaysConfig:
    return PlaysConfig.model_validate(_read("plays.yaml"))
