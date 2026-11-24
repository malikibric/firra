"""Loads a generated corpus into a workspace through the real ingest path.

Messages and meetings go through the same Ingestor as provider data, so minimization, the
metadata-only policy, cleaning and the `pending` marking apply exactly as in production. Each
message lands in the mailbox of its first firm participant whose mailbox is still connected at the
message's time (see firra.synthetic.facts.mailbox_key); mail only a revoked mailbox would have seen
is never ingested, and that mailbox's connection ends revoked (coverage control, p2b D4).
"""

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.directory import load_directory
from firra.db.records import RecordRepo
from firra.db.synthetic import revoke_mailbox, set_up
from firra.db.types import WorkspaceId
from firra.ingest.pipeline import Ingestor
from firra.storage.raw import RawStore
from firra.synthetic.facts import end_of, mailbox_key
from firra.synthetic.spec import SPEC_PATH, Spec
from firra_contracts import CalendarEventRecord, Message

CORPUS_DIR = SPEC_PATH.parent / "corpus"
MESSAGES_FILE = "messages.jsonl"
EVENTS_FILE = "events.jsonl"
MANIFEST_FILE = "manifest.json"


def read_messages(corpus_dir: Path = CORPUS_DIR) -> list[Message]:
    lines = (corpus_dir / MESSAGES_FILE).read_text().splitlines()
    return [Message.model_validate_json(line) for line in lines if line.strip()]


def read_events(corpus_dir: Path = CORPUS_DIR) -> list[CalendarEventRecord]:
    lines = (corpus_dir / EVENTS_FILE).read_text().splitlines()
    return [CalendarEventRecord.model_validate_json(line) for line in lines if line.strip()]


def load_corpus(
    pool: ConnectionPool[Connection[DictRow]],
    raw: RawStore,
    spec: Spec,
    *,
    workspace_id: WorkspaceId,
    messages: Iterable[Message],
    events: Iterable[CalendarEventRecord],
) -> dict[str, Any]:
    """Sets the workspace up from the spec and ingests the corpus; returns the ingest counters."""
    workspace = set_up(pool, workspace_id=workspace_id, spec=spec)
    directory = load_directory(pool, workspace_id=workspace_id)
    records = RecordRepo(pool)
    mailboxes = {
        key: Ingestor(workspace_id, connection_id, directory, records, raw)
        for key, connection_id in workspace.mailboxes.items()
    }
    domain = spec.firm.domain
    not_ingested = 0
    for message in messages:
        participants = [message.sender, *message.to, *message.cc]
        firm = [
            p.email.split("@")[0] for p in participants if p.email.lower().endswith(f"@{domain}")
        ]
        key = mailbox_key(spec, firm, message.occurred_at.date())
        if key is None:
            not_ingested += 1
            continue
        mailboxes[key].message(message)
    for person in spec.firm.people:
        if person.mailbox_revoked_at is not None:
            revoke_mailbox(
                pool,
                workspace_id=workspace_id,
                connection_id=workspace.mailboxes[person.key],
                at=end_of(person.mailbox_revoked_at),
            )
    calendar = Ingestor(workspace_id, workspace.calendar_connection_id, directory, records, raw)
    # Meetings before the dataset's as-of date count as held, later ones as scheduled.
    now = datetime.combine(spec.as_of, time(23, 59), tzinfo=UTC)
    for event in events:
        calendar.calendar_event(event, now=now)
    return {
        "clients": len(workspace.clients),
        "messages": _merged([m.stats.as_progress() for m in mailboxes.values()]),
        "not_ingested": not_ingested,
        "events": calendar.stats.as_progress(),
    }


def _merged(progress: list[dict[str, Any]]) -> dict[str, Any]:
    dropped: Counter[str] = Counter()
    for p in progress:
        dropped.update(p["dropped"])
    totals = {k: sum(p[k] for p in progress) for k in ("processed", "kept", "updated", "invalid")}
    return {**totals, "dropped": dict(sorted(dropped.items()))}
