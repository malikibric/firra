"""Card facts (deterministic) and the model's wording (prompt cards.v1)."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Fact(BaseModel):
    """One deterministic statement a card may rest on; numbers come from ``inputs``."""

    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    basis: Literal["evidence", "metadata"]
    evidence_ids: list[UUID] = Field(default_factory=list)
    inputs: dict[str, Any] | None = None
    # Verified quote text for the model's understanding only; never copied into the card.
    quotes: list[str] = Field(default_factory=list)

    def stored(self) -> dict[str, Any]:
        """The shape stored in ``decision_card.facts`` (no quote text)."""
        row: dict[str, Any] = {"id": self.id, "text": self.text, "basis": self.basis}
        if self.basis == "evidence":
            row["evidence_ids"] = [str(e) for e in self.evidence_ids]
        else:
            row["inputs"] = self.inputs or {}
        return row


class CardText(BaseModel):
    """Structured output of cards.v1."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(max_length=120)
    interpretation: str = Field(max_length=600)
    recommendation: str = Field(max_length=600)
    fact_ids: list[str] = Field(max_length=20)
