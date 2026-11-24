"""Versioned latent demand thresholds in the shared signals configuration file."""

from functools import cache
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from firra.signals.config import config_dir


class NeedsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    similarity: float = Field(ge=0, le=1)
    window_days: int = Field(gt=0)
    min_mentions: int = Field(gt=0)
    min_threads: int = Field(gt=0)
    service_similarity: float = Field(ge=0, le=1)
    embedding_dimensions: int = Field(gt=0)


@cache
def load_config() -> NeedsConfig:
    raw: dict[str, Any] = yaml.safe_load((config_dir() / "signals.yaml").read_text())
    return NeedsConfig.model_validate(raw["latent_demand"])
