"""Signal rule configuration from config/signals.yaml (P2 D10).

The container copies the directory to /app/config and points FIRRA_CONFIG_DIR at it; in a source
checkout the directory next to the package is used. Structure is checked here; each rule validates
its own parameters (see engine.py), so an unknown key fails loudly instead of being ignored.
"""

import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, model_validator

from firra import resources

# The rule catalog (docs/architecture.md § 4 + p2b D3). Every entry must appear in the YAML.
KNOWN_RULES = (
    "delivery_complaint",
    "repeated_complaint",
    "executive_escalation",
    "tone_decline",
    "champion_departure",
    "champion_latency_drift",
    "new_senior_stakeholder",
    "reduced_engagement",
    "meeting_cancellations",
    "late_commitment",
    "goal_result_mismatch",
    "first_90_days_no_result",
    "in_house_risk",
    "renewal_window",
    # P2b (the design notes)
    "client_initiation_drop",
    "client_reply_latency_drift",
    "participant_narrowing",
    "decision_maker_silence",
    "meeting_attendance_drop",
    "scope_cut_talk",
    "expansion_need",
    "client_initiative",
    "budget_growth",
)


class PartialViewConfig(BaseModel):
    """Spec section 5: the owner is not on every thread (``signals.v3``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    window_days: int = 120
    min_other_firm_people: int = 2
    max_owner_share: float = 0.5


class SignalsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    # Consecutive client chat records at most this many minutes apart are one touch.
    chat_burst_minutes: int = 30
    partial_view: PartialViewConfig = PartialViewConfig()
    rules: dict[str, dict[str, Any]]
    # Owned by the latent-demand engine (firra/needs); only carried here so one file holds both.
    latent_demand: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _rules_match_catalog(self) -> "SignalsConfig":
        unknown = sorted(set(self.rules) - set(KNOWN_RULES))
        if unknown:
            raise ValueError(f"unknown signal rules: {', '.join(unknown)}")
        missing = sorted(set(KNOWN_RULES) - set(self.rules))
        if missing:
            raise ValueError(f"signal rules missing from config: {', '.join(missing)}")
        for name, params in self.rules.items():
            if not isinstance(params.get("enabled"), bool):
                raise ValueError(f"rule {name} needs an explicit enabled: true|false")
        return self


def config_dir() -> Path:
    configured = os.environ.get("FIRRA_CONFIG_DIR")
    return Path(configured) if configured else resources.config_dir()


@cache
def load_config() -> SignalsConfig:
    return SignalsConfig.model_validate(yaml.safe_load((config_dir() / "signals.yaml").read_text()))
