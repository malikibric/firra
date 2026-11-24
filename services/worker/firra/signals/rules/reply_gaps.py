"""Client reply gaps per source (spec section 3): email wall-clock hours, chat working hours."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from firra.signals.facts import ClientFacts, RecordFact, Source
from firra.signals.rules.base import in_window
from firra.signals.working_hours import working_hours_between


@dataclass(frozen=True, slots=True)
class Reply:
    at: datetime
    gap: float  # hours (email) or working hours (chat)
    author: UUID | None
    recent: bool
    baseline: bool


def replies(
    facts: ClientFacts, source: Source, *, window_days: int, baseline_days: int
) -> list[Reply]:
    """A reply is a client record whose previous record in the same thread (same source) is a
    firm-side outbound record."""
    previous_by_thread: dict[str, RecordFact] = {}
    found: list[Reply] = []
    for record in sorted(facts.records, key=lambda r: (r.occurred_at, str(r.id))):
        if record.source != source or record.occurred_at > facts.ref or record.thread_key is None:
            continue
        previous = previous_by_thread.get(record.thread_key)
        previous_by_thread[record.thread_key] = record
        if (
            record.direction != "in"
            or record.author_side != "client"
            or previous is None
            or previous.author_side != "firm"
            or previous.direction != "out"
        ):
            continue
        if source == "chat":
            gap = working_hours_between(previous.occurred_at, record.occurred_at, facts.timezone)
        else:
            gap = (record.occurred_at - previous.occurred_at).total_seconds() / 3600
        recent = in_window(record.occurred_at, facts.ref, newer_than_days=window_days)
        baseline = not recent and in_window(
            record.occurred_at, facts.ref, newer_than_days=baseline_days, up_to_days=window_days
        )
        found.append(Reply(record.occurred_at, gap, record.author_person_id, recent, baseline))
    return found
