from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from firra.signals.facts import ClientFacts, ExtractionFact
from firra.signals.model import Family, SignalDraft


class RuleConfig(BaseModel):
    """Base of every rule's parameters: unknown keys are errors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    family: Family


@dataclass(frozen=True, slots=True)
class RuleSpec:
    config_model: type[RuleConfig]
    evaluate: Callable[[ClientFacts, Any], list[SignalDraft]]
    uses_volume: bool = False


def in_window(t: datetime, ref: datetime, *, newer_than_days: int, up_to_days: int = 0) -> bool:
    """``ref - newer_than_days < t <= ref - up_to_days`` (windows never include the future)."""
    return ref - timedelta(days=newer_than_days) < t <= ref - timedelta(days=up_to_days)


def newest(items: Sequence[ExtractionFact]) -> list[ExtractionFact]:
    return sorted(items, key=lambda item: (item.occurred_at, str(item.id)), reverse=True)


def evidence_ids(items: Sequence[ExtractionFact], max_items: int) -> tuple[UUID, ...]:
    return tuple(
        dict.fromkeys(eid for item in newest(items)[:max_items] for eid in item.evidence_ids)
    )
