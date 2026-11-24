"""Frozen inputs and results for the Northbeam evaluation."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from firra.assessment.config import ExpansionState, Play, RiskState


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class EvidenceObs(FrozenModel):
    evidence_id: UUID
    external_id: str
    quote: str


class ItemObs(FrozenModel):
    external_id: str
    kind: str
    quote: str
    subtype: str | None = None
    budget_direction: str | None = None
    initiative_type: str | None = None


class SignalObs(FrozenModel):
    client: str
    type: str
    evidence_ids: list[UUID]


class NeedObs(FrozenModel):
    client: str
    topic: str
    mentions: int
    threads: int
    service: str | None


class CardObs(FrozenModel):
    client: str
    kind: str
    source_signal_types: list[str]
    # Violation classes from re-checking the stored card against its stored facts, e.g.
    # ``number:title``; never the text or number itself.
    integrity_violations: list[str] = Field(default_factory=list)


class UsageObs(FrozenModel):
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class EvalInputs(FrozenModel):
    evidence: list[EvidenceObs] = Field(default_factory=list)
    items: list[ItemObs] = Field(default_factory=list)
    signals: list[SignalObs] = Field(default_factory=list)
    needs: list[NeedObs] = Field(default_factory=list)
    cards: list[CardObs] = Field(default_factory=list)
    usage: list[UsageObs] = Field(default_factory=list)
    verify_dropped: int = 0
    items_attempted: int = 0


class TruthItem(FrozenModel):
    external_id: str
    kind: str
    quote: str
    subtype: str | None = None
    budget_direction: str | None = None
    initiative_type: str | None = None


class TruthNeed(FrozenModel):
    topic: str
    mentions: int
    threads: int
    service: str | None


class TruthAssessment(FrozenModel):
    risk: RiskState
    expansion: ExpansionState
    play: Play
    by: date | None = None


class TruthClient(FrozenModel):
    name: str
    status: str
    signals: list[str]
    latent_needs: list[TruthNeed]
    no_latent_needs: list[str]
    assessment: TruthAssessment | None = None
    caveats: list[str] = Field(default_factory=list)
    detect_by: date | None = None
    max_risk: RiskState | None = None


class AssessmentObs(FrozenModel):
    """One weekly replay assessment; ``client`` is the client's name, mapped to a key by truth."""

    client: str
    as_of: date
    risk: RiskState
    expansion: ExpansionState
    play: Play
    caveats: tuple[str, ...] = ()


class ForbiddenQuote(FrozenModel):
    external_id: str
    quote: str


class Truth(FrozenModel):
    clients: dict[str, TruthClient]
    items: list[TruthItem]
    forbidden_quotes: list[ForbiddenQuote]


class PrecisionRecall(FrozenModel):
    expected: int
    observed: int
    matched: int
    recall: float
    precision: float


class TokenCost(FrozenModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal(0)


class EvalResult(FrozenModel):
    citation_validity: float
    citation_valid: int
    citation_total: int
    extraction: PrecisionRecall
    extraction_by_kind: dict[str, PrecisionRecall]
    injection_count: int
    signals: PrecisionRecall
    signals_by_type: dict[str, PrecisionRecall]
    unverified_signal_count: int
    latent_needs_correct: int
    latent_needs_expected: int
    prohibited_needs: int
    false_positive_cards: int
    false_positive_cards_per_10: float
    cards_checked: int
    cards_expected: int
    card_integrity_failures: int
    verify_drop_rate: float
    usage_by_model: dict[str, TokenCost]
    usage_total: TokenCost
    failures: dict[str, list[str]]
    passed: bool


class ReplayEvalResult(FrozenModel):
    """Timing-aware scores from one replay run; client keys and numbers only."""

    risk_correct: int
    risk_checked: int
    expansion_correct: int
    expansion_checked: int
    play_correct: int
    play_checked: int
    by_met: dict[str, bool]
    churned_detected: int
    churned_checked: int
    lead_days: dict[str, int | None]
    median_lead_days: float | None
    max_risk_exceeded: list[str]
    caveats_missing: dict[str, list[str]]
    false_alarm_weeks_per_10_per_month: float
    flicker: int
    failures: dict[str, list[str]]
    passed: bool
