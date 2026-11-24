"""One pending record through clean → extract → verify → persist (P2 D4-D7).

Only ``full`` email records of ``full`` clients reach the model; everything else gets a final
state without a call. A record the model cannot answer for (invalid output after the retry, or a
blocked answer) is marked ``error`` and counted; the budget stop propagates to the caller.
"""

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from firra.clean import clean_email
from firra.db.processing import ItemRow, PendingRecord, ProcessingRepo
from firra.db.types import WorkspaceId
from firra.extract.items import RecordFacts, accept_items
from firra.extract.schema import ExtractionOutput
from firra.llm.prompts import Prompt
from firra.llm.service import Llm, StructuredOutputError
from firra.llm.untrusted import wrap_untrusted
from firra.llm.vertex import LlmBlockedError
from firra.logging import get_logger

log = get_logger(__name__)

PROMPT_VERSION = "extract.v5"
CONTEXT_MESSAGES = 2
# About 6k tokens for the target and 1k per context message; quotes verify against the full body.
MAX_BODY_CHARS = 24_000
MAX_CONTEXT_CHARS = 4_000

_DIRECTION = {
    "in": "in (client to firm)",
    "out": "out (firm to client)",
    "internal": "internal (within the firm)",
}


@dataclass
class ProcessStats:
    extracted: int = 0  # records sent to the model
    items: int = 0
    empty: int = 0
    deduplicated: int = 0
    not_applicable: int = 0
    skipped_purged: int = 0
    errors: int = 0
    changed: int = 0  # re-ingested while processing; picked up again
    dropped: Counter[str] = field(default_factory=Counter)

    @classmethod
    def from_progress(cls, progress: dict[str, Any]) -> "ProcessStats":
        """Restores counters saved by an earlier continuation of the same job."""
        counters = {
            name: int(progress.get(name, 0))
            for name in (
                "extracted",
                "items",
                "empty",
                "deduplicated",
                "not_applicable",
                "skipped_purged",
                "errors",
                "changed",
            )
        }
        dropped = Counter({str(k): int(v) for k, v in dict(progress.get("dropped", {})).items()})
        return cls(**counters, dropped=dropped)

    def as_progress(self) -> dict[str, Any]:
        return {
            "extracted": self.extracted,
            "items": self.items,
            "empty": self.empty,
            "deduplicated": self.deduplicated,
            "not_applicable": self.not_applicable,
            "skipped_purged": self.skipped_purged,
            "errors": self.errors,
            "changed": self.changed,
            "dropped": dict(sorted(self.dropped.items())),
        }


def build_user_message(record: PendingRecord, body: str, context: list[str]) -> str:
    sender = record.author_email or "unknown"
    side = f" ({record.author_side} side)" if record.author_side else ""
    header = (
        f"Direction: {_DIRECTION[record.direction]}\n"
        f"From: {sender}{side}\n"
        f"Date: {record.occurred_at.date().isoformat()}\n"
        f"Subject: {record.subject or ''}"
    )
    blocks = [
        wrap_untrusted(f"context-{n}", text[:MAX_CONTEXT_CHARS])
        for n, text in enumerate(context, start=1)
    ]
    blocks.append(wrap_untrusted("target", f"{header}\n\n{body[:MAX_BODY_CHARS]}"))
    return "\n\n".join(blocks) + "\n\nExtract the items from the target email."


@dataclass
class RecordProcessor:
    llm: Llm
    repo: ProcessingRepo
    prompt: Prompt
    workspace_id: WorkspaceId
    job_id: UUID | None
    stats: ProcessStats = field(default_factory=ProcessStats)

    def _finish(
        self,
        record: PendingRecord,
        cleaned: str | None,
        rows: list[ItemRow],
    ) -> None:
        saved = self.repo.save(
            workspace_id=self.workspace_id,
            record=record,
            cleaned_body=cleaned,
            items=rows,
            model=self.llm.models.model_for("extract"),
            prompt_version=self.prompt.version,
        )
        if not saved:
            self.stats.changed += 1

    def process(self, record: PendingRecord) -> None:
        ws = self.workspace_id
        if (
            record.kind != "email"
            or record.policy != "full"
            or record.client_policy != "full"
            or record.client_id is None
        ):
            self.repo.mark(workspace_id=ws, record_id=record.id, state="not_applicable")
            self.stats.not_applicable += 1
            return
        if record.body_clean is None:
            self.repo.mark(workspace_id=ws, record_id=record.id, state="skipped_purged")
            self.stats.skipped_purged += 1
            return
        clean = clean_email(record.body_clean)
        body = clean.body
        cleaned = body if body != record.body_clean else None
        digest = hashlib.sha256(body.encode()).digest()
        if body and self.repo.is_duplicate(workspace_id=ws, record=record, content_hash=digest):
            self.repo.mark(workspace_id=ws, record_id=record.id, state="done")
            self.stats.deduplicated += 1
            return
        if not body:
            self.stats.empty += 1
            self._finish(record, cleaned, [])
            return

        context = [
            clean_email(text).body
            for text in self.repo.thread_context(
                workspace_id=ws, record=record, limit=CONTEXT_MESSAGES
            )
        ]
        try:
            answer = self.llm.structured(
                workspace_id=ws,
                job_id=self.job_id,
                purpose="extract",
                prompt_version=self.prompt.version,
                system=self.prompt.text,
                user=build_user_message(record, body, [c for c in context if c]),
                output=ExtractionOutput,
                thinking_budget=0,
            )
        except (StructuredOutputError, LlmBlockedError) as exc:
            self.repo.mark(workspace_id=ws, record_id=record.id, state="error")
            self.stats.errors += 1
            log.warning("extract.record_failed", error_class=type(exc).__name__)
            return

        self.stats.extracted += 1
        accepted, dropped = accept_items(
            answer.value,
            RecordFacts(
                body=body,
                direction=record.direction,
                author_side=record.author_side,
                participant_emails=record.participant_emails,
            ),
        )
        self.stats.dropped.update(dropped)
        self.stats.items += len(accepted)
        rows = [
            ItemRow(
                kind=item.kind,
                payload=item.payload,
                quote=item.evidence.quote,
                locator=item.evidence.locator,
            )
            for item in accepted
        ]
        self._finish(record, cleaned, rows)
