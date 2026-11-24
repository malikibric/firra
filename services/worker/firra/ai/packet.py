"""Packets in repository order, using packet-local handles only.

Caller-configurable limits: ``batch_records`` (default 20, maximum 40), ``max_chars``
(default 60,000, minimum 512). Records outside ``handles`` stay pending. A single
oversized record is shortened and included, retaining valid block delimiters.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from firra.db.processing import PendingRecord
from firra.extract.process import CONTEXT_MESSAGES, MAX_BODY_CHARS, MAX_CONTEXT_CHARS
from firra.llm.untrusted import wrap_untrusted


@dataclass(frozen=True, slots=True)
class Packet:
    packet_id: UUID
    handles: dict[str, UUID]
    text: str
    prompt_version: str


def _block(record: PendingRecord, handle: str, context: Sequence[str], budget: int) -> str:
    side = f" ({record.author_side} side)" if record.author_side else ""
    sender = getattr(record, "author_name", None) or record.author_email or "unknown"
    if record.kind == "chat":
        header = (
            f"Channel: {getattr(record, 'channel', None) or 'unknown'}\n"
            f"From: {sender}{side}\nThread: {'yes' if record.thread_key else 'no'}"
        )
    else:
        header = f"From: {sender}{side}\nSubject: {record.subject or ''}"
    # Bound header text too; neutralise it with the body using the shared helper.
    header = header[: min(MAX_CONTEXT_CHARS, max(0, budget // 4))]
    attributes = {
        "source": "slack" if record.kind == "chat" else "email",
        "direction": record.direction,
        "date": record.occurred_at.date().isoformat(),
    }
    empty = wrap_untrusted(handle, f"{header}\n\n", attributes=attributes)
    room = max(0, budget - len(empty))
    body = (record.body_clean or "")[: min(MAX_BODY_CHARS, room)]
    source = wrap_untrusted(handle, f"{header}\n\n{body}", attributes=attributes)
    blocks = [source]
    used = len(source)
    for text in context[:CONTEXT_MESSAGES]:
        block = wrap_untrusted(
            None, text[:MAX_CONTEXT_CHARS], tag="context_record", attributes={"for": handle}
        )
        if used + len(block) + 2 > budget:
            break
        blocks.append(block)
        used += len(block) + 2
    return "\n\n".join(blocks)


def build_packet(
    records: Sequence[PendingRecord],
    context: Mapping[UUID, Sequence[str]],
    *,
    prompt_version: str,
    batch_records: int = 20,
    max_chars: int = 60_000,
) -> Packet:
    if not 1 <= batch_records <= 40 or max_chars < 512:
        raise ValueError("packet limits require 1..40 records and at least 512 characters")
    handles: dict[str, UUID] = {}
    blocks: list[str] = []
    used = 0
    for record in records[:batch_records]:
        handle = f"r{len(handles) + 1}"
        block = _block(record, handle, context.get(record.id, []), max_chars)
        if used + len(block) + (2 if blocks else 0) > max_chars:
            break
        handles[handle] = record.id
        blocks.append(block)
        used += len(block) + (2 if len(blocks) > 1 else 0)
    return Packet(uuid4(), handles, "\n\n".join(blocks), prompt_version)
