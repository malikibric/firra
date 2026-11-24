"""Deterministic checks on model output before anything is stored (P2 D5, D6).

The model only proposes items. An item is kept when its quote verifies verbatim against the
record's cleaned body and its fields fit its kind; everything else is dropped and counted by
reason, never repaired. Tone items are kept only for client-authored messages (product rule 4:
text of the client's messages, per account, never a person score); so are initiatives, which
describe the client's own business (P2b D10). A ``need`` item (a statement of need without an
ask) is also only taken from client-authored messages and is stored as a ``request``.
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from firra.extract.schema import ExtractedItem, ExtractionOutput
from firra.verify import BodyIndex, Rejected, VerifiedQuote

Direction = Literal["in", "out", "internal"]


@dataclass(frozen=True, slots=True)
class RecordFacts:
    """What validation needs to know about the record the items came from."""

    body: str
    direction: Direction
    author_side: Literal["client", "firm"] | None
    participant_emails: frozenset[str]


@dataclass(frozen=True, slots=True)
class AcceptedItem:
    kind: str
    payload: dict[str, Any]
    evidence: VerifiedQuote


def _clean(value: str | None) -> str | None:
    text = " ".join((value or "").split())
    return text or None


def _due_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _payload(item: ExtractedItem, speaker: str) -> dict[str, Any] | str:
    """Kind-specific payload, or the drop reason when a required field is missing."""
    text = _clean(item.text)
    base: dict[str, Any] = {"speaker": speaker}
    if item.kind == "tone":
        if item.tone is None:
            return "missing_field"
        if item.tone == "neutral":
            return "tone_neutral"
        return {**base, "tone": item.tone}
    if text is None:
        return "missing_field"
    base["text"] = text
    if item.kind in ("request", "need") and item.topic_key is not None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,59}", item.topic_key):
            return "invalid_topic_key"
        base["topic_key"] = item.topic_key
    match item.kind:
        case "complaint":
            return {**base, "subtype": item.subtype or "other"}
        case "commitment":
            if item.owner_side is None:
                return "missing_field"
            return {**base, "owner_side": item.owner_side, "due_date": _due_date(item.due_date)}
        case "goal" | "result_reported":
            return {**base, "metric": _clean(item.metric)}
        case "stakeholder_change":
            if item.change is None:
                return "missing_field"
            return {
                **base,
                "change": item.change,
                "person_name": _clean(item.person_name),
                "role_title": _clean(item.role_title),
            }
        case "renewal_or_budget_talk":
            if item.budget_direction is None:
                return "missing_field"
            return {**base, "budget_direction": item.budget_direction}
        case "initiative":
            if item.initiative_type is None:
                return "missing_field"
            return {**base, "initiative_type": item.initiative_type}
        case "need":
            return {**base, "form": "need"}
        case _:
            return base


def accept_items(
    output: ExtractionOutput, record: RecordFacts
) -> tuple[list[AcceptedItem], Counter[str]]:
    """Items that passed every check, plus drop counts by reason."""
    index = BodyIndex(record.body)
    accepted: list[AcceptedItem] = []
    dropped: Counter[str] = Counter()
    seen: set[tuple[str, int, int]] = set()
    client_authored = record.direction == "in" and record.author_side == "client"

    for item in output.items:
        if item.kind == "tone" and not client_authored:
            dropped["tone_not_client"] += 1
            continue
        if item.kind == "initiative" and not client_authored:
            dropped["initiative_not_client"] += 1
            continue
        if item.kind == "need" and not client_authored:
            dropped["need_not_client"] += 1
            continue
        verified = index.verify(item.quote)
        if isinstance(verified, Rejected):
            dropped[f"quote_{verified.reason}"] += 1
            continue
        speaker = item.speaker.strip().lower()
        payload = _payload(item, speaker if speaker in record.participant_emails else "unknown")
        if isinstance(payload, str):
            dropped[payload] += 1
            continue
        # A statement of need is stored as a request (``form: need``), so the latent-demand engine
        # and the request memory need no change, and an ask and a need on one span count once.
        stored_kind = "request" if item.kind == "need" else item.kind
        key = (stored_kind, verified.start, verified.end)
        if key in seen:
            dropped["duplicate"] += 1
            continue
        if item.kind == "tone" and any(a.kind == "tone" for a in accepted):
            dropped["duplicate"] += 1
            continue
        seen.add(key)
        accepted.append(AcceptedItem(kind=stored_kind, payload=payload, evidence=verified))
    return accepted, dropped
