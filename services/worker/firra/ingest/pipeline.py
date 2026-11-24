"""Turns canonical records into stored rows, applying minimization before anything is written.

Dropped records leave no trace except a counter. ``metadata_only`` records keep participants,
timestamps and counts only: no subject, body, title, content hash or raw object.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from firra.clean import clean_email
from firra.db.records import EventInput, PersonInput, RecordInput, RecordRepo
from firra.db.types import WorkspaceId
from firra.memory.rules import is_decision_maker, load_config, title_from_signature
from firra.policy.minimize import Directory, Drop, client_for, decide, is_firm, needs_content
from firra.storage.raw import RawStore
from firra_contracts import CalendarEventRecord, Message


class _Person(Protocol):
    """Participant shape shared by every canonical record type."""

    @property
    def email(self) -> str: ...

    @property
    def name(self) -> str | None: ...


EventStatus = Literal["scheduled", "held", "cancelled", "rescheduled"]


def word_count(text: str | None) -> int:
    return len(text.split()) if text else 0


@dataclass
class IngestStats:
    kept: int = 0
    updated: int = 0
    invalid: int = 0
    dropped: Counter[str] = field(default_factory=Counter)

    @property
    def processed(self) -> int:
        return self.kept + self.updated + self.invalid + sum(self.dropped.values())

    @classmethod
    def from_progress(cls, progress: dict[str, Any]) -> "IngestStats":
        """Restores counters saved by an earlier continuation of the same job."""
        return cls(
            kept=int(progress.get("kept", 0)),
            updated=int(progress.get("updated", 0)),
            invalid=int(progress.get("invalid", 0)),
            dropped=Counter({str(k): int(v) for k, v in dict(progress.get("dropped", {})).items()}),
        )

    def as_progress(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "kept": self.kept,
            "updated": self.updated,
            "invalid": self.invalid,
            "dropped": dict(sorted(self.dropped.items())),
        }


@dataclass
class Ingestor:
    workspace_id: WorkspaceId
    connection_id: UUID
    directory: Directory
    records: RecordRepo
    raw: RawStore
    stats: IngestStats = field(default_factory=IngestStats)

    def _people(
        self, participants: list[_Person], seen_at: datetime
    ) -> tuple[dict[str, UUID], int]:
        """Stores client and firm participants; others are counted, never stored."""
        ids: dict[str, UUID] = {}
        others = 0
        for person in participants:
            email = person.email.lower()
            if email in ids:
                continue
            client = client_for(email, self.directory)
            if client is not None:
                side_input = PersonInput(email, person.name, "client", client.id)
            elif is_firm(email, self.directory):
                side_input = PersonInput(email, person.name, "firm", None)
            else:
                others += 1
                continue
            ids[email] = self.records.upsert_person(
                workspace_id=self.workspace_id, person=side_input, seen_at=seen_at
            )
        return ids, others

    def _raw_name(self, external_id: str) -> str:
        digest = hashlib.sha256(external_id.encode()).hexdigest()
        return f"raw/{self.workspace_id}/{self.connection_id}/{digest}.json"

    def _count(self, inserted: bool) -> None:
        if inserted:
            self.stats.kept += 1
        else:
            self.stats.updated += 1

    def needs_content(self, message: Message) -> bool:
        """False when minimization drops ``message`` on its participants alone."""
        recipients = [*message.to, *message.cc, *message.bcc]
        return needs_content(message.sender.email, [p.email for p in recipients], self.directory)

    def message(self, message: Message) -> None:
        recipients = [*message.to, *message.cc, *message.bcc]
        mention_text = (
            f"{message.subject or ''}\n{message.body_text or ''}"
            if self.directory.internal_mentions
            else ""
        )
        decision = decide(
            message.sender.email,
            [p.email for p in recipients],
            self.directory,
            mention_text=mention_text,
        )
        if isinstance(decision, Drop):
            self.stats.dropped[decision.reason] += 1
            return

        people, others = self._people([message.sender, *recipients], message.occurred_at)
        metadata: dict[str, Any] = {
            "participant_count": len(recipients) + 1,
            "unmapped_participant_count": others,
        }
        # Only what the author wrote: quoted history, signatures and disclaimers never persist.
        clean = clean_email(message.body_text or "")
        cleaned = clean.body
        if decision.policy == "full" and decision.direction == "in":
            author_id = people.get(message.sender.email.lower())
            title = title_from_signature(clean.signature, message.sender.name)
            if author_id is not None and title is not None:
                self.records.set_client_title(
                    workspace_id=self.workspace_id,
                    client_id=decision.client.id,
                    person_id=author_id,
                    title=title,
                    decision_maker=is_decision_maker(title, load_config()),
                    observed_at=message.occurred_at,
                )
        body = raw_uri = content_hash = None
        if decision.policy == "full":
            metadata["subject"] = message.subject
            body = cleaned or None
            content_hash = hashlib.sha256((body or "").encode()).digest()
            raw_uri = self.raw.put(
                self._raw_name(message.external_id),
                message.model_dump_json().encode(),
                "application/json",
            )

        record = RecordInput(
            connection_id=self.connection_id,
            provider=message.provider.value,
            external_id=message.external_id,
            kind="email",
            client_id=decision.client.id,
            policy=decision.policy,
            thread_key=message.thread_key,
            occurred_at=message.occurred_at,
            direction=decision.direction,
            word_count=word_count(cleaned),
            metadata=metadata,
            body_clean=body,
            raw_object_uri=raw_uri,
            deep_link=message.deep_link,
            content_hash=content_hash,
        )
        _, inserted = self.records.upsert_record(
            workspace_id=self.workspace_id,
            record=record,
            author_person_id=people.get(message.sender.email.lower()),
            participants=list(people.values()),
        )
        self._count(inserted)

    def calendar_event(self, event: CalendarEventRecord, *, now: datetime) -> None:
        decision = decide(event.organizer.email, [a.email for a in event.attendees], self.directory)
        if isinstance(decision, Drop):
            self.stats.dropped[decision.reason] += 1
            return

        people, others = self._people([event.organizer, *event.attendees], event.starts_at)
        duration = max(0, int((event.ends_at - event.starts_at).total_seconds() // 60))
        status: EventStatus
        if event.status.value == "cancelled":
            status = "cancelled"
        elif event.rescheduled:
            status = "rescheduled"
        else:
            status = "held" if event.ends_at <= now else "scheduled"

        metadata: dict[str, Any] = {
            "attendee_count": len(event.attendees),
            "unmapped_participant_count": others,
            "duration_min": duration,
        }
        raw_uri = None
        if decision.policy == "full":
            metadata["title"] = event.title
            raw_uri = self.raw.put(
                self._raw_name(event.external_id),
                json.dumps(event.model_dump(mode="json")).encode(),
                "application/json",
            )

        record = RecordInput(
            connection_id=self.connection_id,
            provider=event.provider.value,
            external_id=event.external_id,
            kind="calendar_event",
            client_id=decision.client.id,
            policy=decision.policy,
            thread_key=None,
            occurred_at=event.starts_at,
            direction=decision.direction,
            word_count=0,
            metadata=metadata,
            body_clean=None,
            raw_object_uri=raw_uri,
            deep_link=event.deep_link,
            content_hash=None,
        )
        _, inserted = self.records.upsert_record(
            workspace_id=self.workspace_id,
            record=record,
            author_person_id=people.get(event.organizer.email.lower()),
            participants=list(people.values()),
            event=EventInput(
                starts_at=event.starts_at,
                duration_min=duration,
                status=status,
                attendee_count=len(event.attendees),
            ),
        )
        self._count(inserted)
