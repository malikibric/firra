"""The only place that maps a purpose to a Gemini model (CLAUDE.md § 7).

Models come from FIRRA_MODEL_* settings; nothing else in the codebase names a model.
"""

from dataclasses import dataclass
from typing import Literal

Purpose = Literal["extract", "synth", "embed", "synthdata"]


class LlmNotConfiguredError(Exception):
    """Vertex location or the model for a purpose is not set in this environment."""


@dataclass(frozen=True, slots=True)
class ModelSet:
    location: str
    extract: str | None
    synth: str | None
    embed: str | None
    synthdata: str | None

    def model_for(self, purpose: Purpose) -> str:
        model: str | None = getattr(self, purpose)
        if not model:
            raise LlmNotConfiguredError(f"no model configured for {purpose}")
        return model
