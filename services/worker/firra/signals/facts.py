"""Everything the rules may look at for one client, as plain immutable data (no I/O).

The loader (firra/db/signals.py) builds it; ``ref`` is the reference time of the run. Facts at
``ref`` equal facts built from a database holding nothing dated after ``ref`` (P2b D5): people,
decision makers, champions, commitment states and needs are all computed as of ``ref``.
``coverage`` is the exception by design: it describes how complete the stored data is (D4), so it
uses each connection's current sync state. ``metadata_only`` clients have no extractions,
commitments or needs.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

Side = Literal["client", "firm"]
Source = Literal["email", "chat"]
CommitmentStatus = Literal["open", "done", "late", "cancelled"]


@dataclass(frozen=True, slots=True)
class PersonFact:
    """A client-side person as of ``ref``: decision maker and champion are computed at ``ref``."""

    id: UUID
    name: str | None
    is_decision_maker: bool
    is_champion: bool
    departed_at: datetime | None
    first_seen_at: datetime | None


@dataclass(frozen=True, slots=True)
class RecordFact:
    id: UUID
    thread_key: str | None
    occurred_at: datetime
    direction: Literal["in", "out", "internal"]
    author_person_id: UUID | None
    author_side: Side | None
    processed: bool
    connection_id: UUID | None = None
    source: Source = "email"


@dataclass(frozen=True, slots=True)
class ExtractionFact:
    id: UUID
    record_id: UUID
    kind: str
    payload: dict[str, Any]
    occurred_at: datetime
    author_person_id: UUID | None
    evidence_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class CommitmentFact:
    id: UUID
    owner_side: Side
    due_date: date | None
    status: CommitmentStatus
    evidence_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class EventFact:
    starts_at: datetime
    status: Literal["scheduled", "held", "cancelled", "rescheduled"]
    attendee_count: int | None = None


@dataclass(frozen=True, slots=True)
class NeedFact:
    """A latent need (F2); only needs whose qualifying window ended by ``ref`` are loaded."""

    id: UUID
    workspace_service_id: UUID | None
    status: Literal["open", "addressed", "dismissed"]
    last_seen_at: datetime
    mention_count: int
    evidence_ids: tuple[UUID, ...]
    # Catalog name of the matched service, so a need can be compared with what the client already
    # buys (``ClientFacts.service_tags``).
    service_name: str | None = None


@dataclass(frozen=True, slots=True)
class CoverageFact:
    """A connection that carried the client's baseline records (days 31-120 before ``ref``).

    ``synced_until`` is ``last_synced_at`` for a connection that is not active, the newest record
    for an upload, and None (open-ended) for an active connection (D4).
    """

    connection_id: UUID
    status: Literal["active", "error", "revoked"]
    synced_until: datetime | None


@dataclass(frozen=True, slots=True)
class ClientFacts:
    client_id: UUID
    status: Literal["active", "churned", "prospect"]
    policy: Literal["full", "metadata_only", "excluded"]
    contract_start: date | None
    renewal_date: date | None
    notice_period_days: int | None
    ref: datetime
    people: tuple[PersonFact, ...] = ()
    records: tuple[RecordFact, ...] = ()
    extractions: tuple[ExtractionFact, ...] = ()
    commitments: tuple[CommitmentFact, ...] = ()
    events: tuple[EventFact, ...] = ()
    needs: tuple[NeedFact, ...] = ()
    coverage: tuple[CoverageFact, ...] = ()
    # Catalog names of the services the client already has (``client.service_tags``).
    service_tags: tuple[str, ...] = ()
    # Workspace time zone (chat latency counts working hours there) and the firm-side people that
    # are the owner (empty = unknown, then the partial-view rule never triggers).
    timezone: str = "UTC"
    owner_person_ids: frozenset[UUID] = field(default_factory=frozenset)

    def buys(self, service_name: str | None) -> bool:
        """Whether the client already has this catalog service (case-insensitive name match)."""
        if service_name is None:
            return False
        wanted = " ".join(service_name.casefold().split())
        return any(" ".join(tag.casefold().split()) == wanted for tag in self.service_tags)

    def client_emails(self) -> tuple[RecordFact, ...]:
        """Client messages that are email (tone is only read from these in ``signals.v3``)."""
        return tuple(r for r in self.client_messages() if r.source == "email")

    def client_touches(self, burst_minutes: int) -> tuple[RecordFact, ...]:
        """Client emails plus client chat records collapsed into bursts, oldest first.

        Consecutive client-authored chat records in the same channel/thread whose gaps are at most
        ``burst_minutes`` are one touch, represented by the first record (spec section 2).
        """
        gap = timedelta(minutes=burst_minutes)
        last_seen: dict[str | None, datetime] = {}
        touches: list[RecordFact] = []
        for record in self.client_messages():
            if record.source == "email":
                touches.append(record)
                continue
            previous = last_seen.get(record.thread_key)
            last_seen[record.thread_key] = record.occurred_at
            if previous is None or record.occurred_at - previous > gap:
                touches.append(record)
        return tuple(touches)

    def client_messages(self) -> tuple[RecordFact, ...]:
        """Inbound records written by a client-side person (any source), oldest first."""
        return tuple(
            sorted(
                (
                    r
                    for r in self.records
                    if r.direction == "in"
                    and r.author_side == "client"
                    and r.occurred_at <= self.ref
                ),
                key=lambda r: r.occurred_at,
            )
        )

    def evidence_ids(self) -> frozenset[UUID]:
        """Every evidence id a draft may cite for this client."""
        ids: set[UUID] = set()
        for extraction in self.extractions:
            ids.update(extraction.evidence_ids)
        for commitment in self.commitments:
            ids.update(commitment.evidence_ids)
        for need in self.needs:
            ids.update(need.evidence_ids)
        return frozenset(ids)
