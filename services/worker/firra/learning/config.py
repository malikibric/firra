"""Validated learning configuration, following the assessment loader."""

from functools import cache
from typing import Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from firra.signals.config import config_dir


class LearningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["learning.v1"]
    horizon_days: int = Field(gt=0, strict=True)
    min_rated: int = Field(gt=0, strict=True)


@cache
def load_config() -> LearningConfig:
    return LearningConfig.model_validate(
        yaml.safe_load((config_dir() / "learning.yaml").read_text())
    )
