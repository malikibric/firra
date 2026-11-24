"""Response schema of the extraction prompt (extract.v2 to v4).

Flat items: kind-specific fields are null when they do not apply, which keeps the schema simple
for structured output."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ExtractionKind = Literal[
    "request",
    "commitment",
    "praise",
    "complaint",
    "goal",
    "stakeholder_change",
    "in_house_or_competitor_mention",
    "renewal_or_budget_talk",
    "result_reported",
    "tone",
    "initiative",
    # Model-facing only: stored as a ``request`` with ``form: need`` (see ``accept_items``).
    "need",
]
ComplaintSubtype = Literal["delay", "quality", "communication", "other"]
Tone = Literal["positive", "neutral", "negative"]
Side = Literal["firm", "client"]
StakeholderChange = Literal["joined", "left", "new_role"]
BudgetDirection = Literal["grow", "cut", "renewal", "neutral"]
InitiativeType = Literal["market", "product", "team", "hire", "rebrand", "funding", "other"]

MAX_ITEMS = 30


class ExtractedItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: ExtractionKind
    quote: str = Field(max_length=600)
    speaker: str = Field(max_length=320)
    text: str = Field(max_length=300)
    subtype: ComplaintSubtype | None = None
    tone: Tone | None = None
    owner_side: Side | None = None
    due_date: str | None = Field(default=None, max_length=10)
    metric: str | None = Field(default=None, max_length=200)
    change: StakeholderChange | None = None
    person_name: str | None = Field(default=None, max_length=200)
    role_title: str | None = Field(default=None, max_length=200)
    budget_direction: BudgetDirection | None = None
    initiative_type: InitiativeType | None = None
    topic_key: str | None = None


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[ExtractedItem] = Field(max_length=MAX_ITEMS)
