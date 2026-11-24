"""Validated, cached routines.v1 configuration."""

from functools import cache
from typing import Annotated, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator

from firra.signals.config import config_dir


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Schedule(_Frozen):
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    weekdays: tuple[Annotated[int, Field(ge=1, le=7)], ...]


class Schedules(_Frozen):
    morning_scan: Schedule
    commitment_watch: Schedule


class RoutinesConfig(_Frozen):
    version: Literal["routines.v1"]
    budget_per_week: int = Field(gt=0)
    wrong_hold_days: int = Field(gt=0)
    notice_close_days: int = Field(gt=0)
    notice_near_days: int = Field(gt=0)
    schedules: Schedules

    @model_validator(mode="after")
    def _notice_order(self) -> "RoutinesConfig":
        if self.notice_near_days < self.notice_close_days:
            raise ValueError("notice_near_days must be at least notice_close_days")
        return self


@cache
def load_config() -> RoutinesConfig:
    return RoutinesConfig.model_validate(
        yaml.safe_load((config_dir() / "routines.yaml").read_text())
    )
