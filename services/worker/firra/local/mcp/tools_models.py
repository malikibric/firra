"""New MCP result contracts pending promotion to packages/contracts by the lead."""

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from firra_contracts.generated_v1.mcp_evidence import Item


class EvidenceResult(Item):
    untrusted_context: str | None = None


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    evidence_ids: list[UUID]
    fields: dict[str, Any]


class PreparationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generated_at: AwareDatetime
    client_id: UUID
    meeting_at: AwareDatetime
    facts: list[Fact]
    question_inputs: list[Fact]


class ExpansionFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: UUID
    client_id: UUID
    expansion_state: Literal["hypothesis", "strong"]
    evidence_ids: list[UUID]
    as_of: str


class LatentNeedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: UUID
    client_id: UUID
    service_id: UUID
    service_name: str
    mention_count: int
    thread_count: int
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    evidence_ids: list[UUID]


class GrowthResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generated_at: AwareDatetime
    expansions: list[ExpansionFact]
    latent_needs: list[LatentNeedFact]
