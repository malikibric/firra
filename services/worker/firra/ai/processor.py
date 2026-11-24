"""Batch extraction through an offline-testable executor, with deterministic verification.

No price, budget, or hosted model usage writes. Executor tokens belong only to job
progress; packet/executor provenance accompanies each stored extraction payload.
"""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from firra.ai.executor import (
    ExecutorFailed,
    ExecutorKind,
    ExecutorLimitReached,
    ExecutorMalformed,
    ExecutorNotInstalled,
    ExecutorNotSignedIn,
    ExecutorResult,
    ExecutorTimeout,
    ExtractionExecutor,
    extraction_schema,
)
from firra.ai.packet import Packet, build_packet
from firra.clean import clean_email
from firra.db.ai_processing import AiProcessingRepo
from firra.db.processing import ItemRow, PendingRecord
from firra.db.types import WorkspaceId
from firra.extract.items import RecordFacts, accept_items
from firra.extract.process import CONTEXT_MESSAGES, ProcessStats
from firra.extract.schema import ExtractionOutput
from firra.llm.prompts import Prompt
from firra.logging import get_logger
from firra.tasks.errors import ContinueTask, PermanentTaskError
from firra_contracts import AiExtractionOutput

PROMPT_VERSION = "extract.v6"
log = get_logger(__name__)


@dataclass
class PacketProcessor:
    executor: ExtractionExecutor | None
    repo: AiProcessingRepo
    prompt: Prompt
    workspace_id: WorkspaceId
    stats: ProcessStats = field(default_factory=ProcessStats)
    timeout_seconds: float = 120
    batch_records: int = 20
    max_chars: int = 60_000
    input_tokens: int = 0
    output_tokens: int = 0
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    resume_at: datetime | None = None
    retry_at: datetime | None = None

    @property
    def kind(self) -> ExecutorKind:
        return self.executor.kind if self.executor is not None else ExecutorKind.conversation

    def progress(self) -> dict[str, Any]:
        return {
            **self.stats.as_progress(),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "resume_at": self.resume_at.isoformat() if self.resume_at else None,
            "retry_at": self.retry_at.isoformat() if self.retry_at else None,
        }

    def _save(self, record: PendingRecord, body: str, rows: list[ItemRow], model: str) -> bool:
        if self.repo.save(
            workspace_id=self.workspace_id,
            record=record,
            cleaned_body=body if body != record.body_clean else None,
            items=rows,
            model=model,
            prompt_version=self.prompt.version,
        ):
            self.stats.items += len(rows)
            return True
        self.stats.changed += 1
        return False

    def _attempt(self, record: PendingRecord, reason: str) -> None:
        state = self.repo.attempt(workspace_id=self.workspace_id, record=record)
        if state is None:
            self.stats.changed += 1
            return
        self.stats.dropped[reason] += 1
        if state == "error":
            self.stats.errors += 1

    def _run(self, packet: Packet) -> tuple[AiExtractionOutput, ExecutorResult]:
        if self.executor is None:
            raise PermanentTaskError("conversation_only")
        for attempt in range(2):
            try:
                result = self.executor.run(
                    self.prompt.text,
                    packet.text,
                    extraction_schema(),
                    timeout_seconds=self.timeout_seconds,
                )
                if result.input_tokens is not None:
                    self.input_tokens += max(0, result.input_tokens)
                if result.output_tokens is not None:
                    self.output_tokens += max(0, result.output_tokens)
                try:
                    output = AiExtractionOutput.model_validate_json(result.raw, strict=True)
                except ValidationError:
                    raise ExecutorMalformed() from None
                return output, result
            except (ExecutorNotInstalled, ExecutorNotSignedIn) as exc:
                raise PermanentTaskError(exc.error_class) from None
            except ExecutorLimitReached as exc:
                delay = (
                    max(0, (exc.resume_at - self.clock()).total_seconds())
                    if exc.resume_at is not None
                    else 60
                )
                self.resume_at = exc.resume_at
                self.retry_at = self.clock() + timedelta(seconds=delay)
                raise ContinueTask(self.progress(), delay_seconds=delay) from None
            except (ExecutorMalformed, ExecutorTimeout, ExecutorFailed):
                if attempt:
                    raise
        raise AssertionError("unreachable")

    def process(self, records: Sequence[PendingRecord]) -> ProcessStats:
        eligible: dict[Any, tuple[PendingRecord, str]] = {}
        context: dict[Any, list[str]] = {}
        ws = self.workspace_id
        for record in records:
            if (
                record.kind not in ("email", "chat")
                or record.policy != "full"
                or record.client_policy != "full"
                or record.client_id is None
            ):
                self.repo.mark(workspace_id=ws, record_id=record.id, state="not_applicable")
                self.stats.not_applicable += 1
                continue
            if record.body_clean is None:
                self.repo.mark(workspace_id=ws, record_id=record.id, state="skipped_purged")
                self.stats.skipped_purged += 1
                continue
            body = (
                clean_email(record.body_clean).body if record.kind == "email" else record.body_clean
            )
            digest = hashlib.sha256(body.encode()).digest()
            if body and self.repo.is_duplicate(workspace_id=ws, record=record, content_hash=digest):
                self.repo.mark(workspace_id=ws, record_id=record.id, state="done")
                self.stats.deduplicated += 1
                continue
            if not body.strip():
                self.stats.empty += 1
                self._save(record, body, [], str(self.kind))
                continue
            eligible[record.id] = (record, body)
            texts = self.repo.thread_context(workspace_id=ws, record=record, limit=CONTEXT_MESSAGES)
            context[record.id] = (
                [clean_email(text).body for text in texts] if record.kind == "email" else texts
            )
        if not eligible:
            return self.stats
        packet = build_packet(
            [replace(record, body_clean=body) for record, body in eligible.values()],
            context,
            prompt_version=self.prompt.version,
            batch_records=self.batch_records,
            max_chars=self.max_chars,
        )
        try:
            output, result = self._run(packet)
        except (ExecutorMalformed, ExecutorTimeout, ExecutorFailed) as exc:
            for record_id in packet.handles.values():
                self._attempt(eligible[record_id][0], type(exc).__name__)
            log.warning(
                "ai.packet_failed",
                packet_id=str(packet.packet_id),
                record_count=len(packet.handles),
                error_class=type(exc).__name__,
            )
            return self.stats
        self.verify_and_store(
            packet, output, records=[record for record, _ in eligible.values()], model=result.model
        )
        return self.stats

    def verify_and_store(
        self,
        packet: Packet,
        output: AiExtractionOutput,
        *,
        records: Sequence[PendingRecord],
        model: str | None = None,
    ) -> "VerificationResult":
        """The executor and conversation share snapshot, quote, policy and provenance checks."""
        before_items, before_changed = self.stats.items, self.stats.changed
        before_dropped = self.stats.dropped.copy()
        done = 0
        eligible = {
            record.id: (
                record,
                clean_email(record.body_clean or "").body
                if record.kind == "email"
                else record.body_clean or "",
            )
            for record in records
        }
        self.stats.extracted += len(packet.handles)
        seen: set[str] = set()
        for answer in output.results:
            if answer.record not in packet.handles:
                self.stats.dropped["unknown_handle"] += 1
                continue
            if answer.record in seen:
                self.stats.dropped["duplicate_handle"] += 1
                continue
            seen.add(answer.record)
            record, body = eligible[packet.handles[answer.record]]
            accepted, dropped = accept_items(
                ExtractionOutput.model_validate(
                    {"items": [item.model_dump(mode="json") for item in answer.items]}
                ),
                RecordFacts(body, record.direction, record.author_side, record.participant_emails),
            )
            self.stats.dropped.update(dropped)
            rows = [
                ItemRow(
                    kind=item.kind,
                    payload={
                        **item.payload,
                        "_provenance": {
                            "executor": str(self.kind),
                            "packet_id": str(packet.packet_id),
                        },
                    },
                    quote=item.evidence.quote,
                    locator=item.evidence.locator,
                )
                for item in accepted
            ]
            done += int(self._save(record, body, rows, model or str(self.kind)))
        for handle, record_id in packet.handles.items():
            if handle not in seen:
                self._attempt(eligible[record_id][0], "no_answer")
        rejected = self.stats.dropped - before_dropped
        changed = self.stats.changed - before_changed
        if changed:
            rejected["policy_or_record_changed"] += changed
        # Counts and reason codes only (never quotes): lets the owner see quality while it runs.
        log.info(
            "ai.packet_processed",
            packet_id=str(packet.packet_id),
            record_count=len(packet.handles),
            handle_count=len(seen),
            items_accepted=self.stats.items - before_items,
            items_rejected=dict(rejected),
        )
        return VerificationResult(
            self.stats.items - before_items, dict(rejected), done, len(packet.handles) - done
        )


@dataclass(frozen=True, slots=True)
class VerificationResult:
    accepted: int
    rejected_by_reason: dict[str, int]
    records_done: int
    records_left: int
