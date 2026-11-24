"""Conversation packet issuance and submissions; every logical call has one MCP audit.

Only the shared PacketProcessor verifies and stores items. Leases, plaintext snapshots and
idempotent result counts live in this engine process, never in the database.
"""

import json
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from firra.ai.executor import extraction_schema
from firra.ai.lease import Lease, LeaseError, LeaseStore
from firra.ai.packet import Packet, build_packet
from firra.ai.processor import PROMPT_VERSION, PacketProcessor, VerificationResult
from firra.clean import clean_email
from firra.db.local_mcp import audit
from firra.db.local_processing import ConversationRepo
from firra.db.processing import PendingRecord
from firra.db.types import WorkspaceId
from firra.extract.process import CONTEXT_MESSAGES
from firra.llm.prompts import load_prompt
from firra.llm.untrusted import UNTRUSTED_NOTICE
from firra.local.readctx import ReadContext
from firra_contracts import AiExtractionOutput, TaskEnvelope


class PendingPacketResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_id: UUID
    expires_at: AwareDatetime
    packet: str
    instructions: str
    output_schema: dict[str, Any]


class SubmissionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted: int = Field(ge=0)
    rejected_by_reason: dict[str, int]
    records_done: int = Field(ge=0)
    records_left: int = Field(ge=0)


class ProcessingTools:
    def __init__(
        self, ctx: ReadContext, *, enqueue: Callable[[TaskEnvelope], None] | None = None
    ) -> None:
        self.ctx = ctx
        self.workspace_id = WorkspaceId(ctx.identity.workspace_id)
        self.leases = LeaseStore(clock=ctx.clock)
        self.prompt = load_prompt(PROMPT_VERSION)
        self.enqueue = enqueue

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        with self.ctx.pool.connection() as conn:
            audit(
                conn,
                workspace_id=self.workspace_id,
                actor_id=self.ctx.identity.user_id,
                name=name,
                count=0,
            )
        # A fresh repo per call avoids sharing its transaction connection between threads.
        repo = ConversationRepo(self.ctx.pool)
        if name == "get_pending_mail":
            limit = args.get("limit", 5)

            def factory() -> tuple[Packet, list[PendingRecord]]:
                records = repo.pending(workspace_id=self.workspace_id, limit=limit)
                cleaned = [
                    replace(record, body_clean=clean_email(record.body_clean or "").body)
                    if record.kind == "email"
                    else record
                    for record in records
                ]
                context = {}
                for record in cleaned:
                    texts = repo.thread_context(
                        workspace_id=self.workspace_id, record=record, limit=CONTEXT_MESSAGES
                    )
                    context[record.id] = (
                        [clean_email(text).body for text in texts]
                        if record.kind == "email"
                        else texts
                    )
                packet = build_packet(
                    cleaned, context, prompt_version=self.prompt.version, batch_records=limit
                )
                return packet, [
                    record for record in records if record.id in packet.handles.values()
                ]

            lease = self.leases.get(
                self.workspace_id,
                factory,
                still_eligible=lambda lease: repo.still_eligible(
                    workspace_id=self.workspace_id, record_ids=list(lease.packet.handles.values())
                ),
            )
            return PendingPacketResult(
                lease_id=lease.id,
                expires_at=lease.expires_at,
                packet=lease.packet.text,
                instructions=f"{self.prompt.text}\n\n{UNTRUSTED_NOTICE}",
                output_schema=extraction_schema(),
            ).model_dump(mode="json")

        lease_id = UUID(str(args["lease_id"]))

        def store(lease: Lease) -> VerificationResult:
            try:
                output = AiExtractionOutput.model_validate_json(
                    json.dumps(args["output"], allow_nan=False), strict=True
                )
            except (ValidationError, ValueError, TypeError):
                raise LeaseError("invalid_output") from None
            processor = PacketProcessor(None, repo, self.prompt, self.workspace_id)
            with repo.submission():
                result = processor.verify_and_store(lease.packet, output, records=lease.records)
                if (
                    repo.remaining(
                        workspace_id=self.workspace_id,
                        record_ids=list(lease.packet.handles.values()),
                    )
                    == 0
                ):
                    repo.request_followup(workspace_id=self.workspace_id, lease_id=lease.id)
                result = replace(
                    result, records_left=repo.remaining(workspace_id=self.workspace_id)
                )

            return result

        result = self.leases.submit(self.workspace_id, lease_id, store)
        followup = repo.followup(workspace_id=self.workspace_id, lease_id=lease_id)
        if followup is not None and self.enqueue is not None:
            # LocalExecutor deduplicates re-dispatch after a queue failure.
            self.enqueue(followup)
        return SubmissionResult.model_validate(asdict(result)).model_dump(mode="json")
