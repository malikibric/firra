"""People and source records written by ingest. Every statement is scoped by workspace_id."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId
from firra.local.bodycrypt import ENCRYPTED_FIELDS, provider_for, write_body, write_field

Side = Literal["client", "firm"]


@dataclass(frozen=True, slots=True)
class PersonInput:
    email: str
    name: str | None
    side: Side
    client_id: UUID | None


@dataclass(frozen=True, slots=True)
class RecordInput:
    connection_id: UUID
    provider: str
    external_id: str
    kind: Literal["email", "calendar_event", "chat"]
    client_id: UUID
    policy: Literal["full", "metadata_only"]
    thread_key: str | None
    occurred_at: datetime
    direction: Literal["in", "out", "internal"]
    word_count: int
    metadata: dict[str, Any]
    body_clean: str | None
    raw_object_uri: str | None
    deep_link: str | None
    content_hash: bytes | None


@dataclass(frozen=True, slots=True)
class EventInput:
    starts_at: datetime
    duration_min: int
    status: Literal["scheduled", "held", "cancelled", "rescheduled"]
    attendee_count: int


_FIND_PERSON = """
select person_id from public.person_identity
 where workspace_id = %(workspace_id)s and kind = 'email' and value = %(email)s
"""

_INSERT_OBSERVATION = """
insert into public.person_observation (workspace_id, client_id, person_id, observed_at, source,
                                       role_title, is_decision_maker)
select p.workspace_id, p.client_id, p.id, %(observed_at)s, 'signature', left(%(title)s, 120),
       %(decision)s
  from public.person p
 where p.workspace_id = %(workspace_id)s and p.client_id = %(client_id)s
   and p.id = %(person_id)s and p.side = 'client'
on conflict (workspace_id, person_id, source, observed_at) do nothing
"""

_TOUCH_PERSON = """
update public.person
   set last_seen_at = greatest(coalesce(last_seen_at, %(seen_at)s), %(seen_at)s),
       first_seen_at = least(coalesce(first_seen_at, %(seen_at)s), %(seen_at)s),
       display_name = coalesce(display_name, %(name)s),
       client_id = coalesce(client_id, %(client_id)s)
 where workspace_id = %(workspace_id)s and id = %(person_id)s
"""

_INSERT_PERSON = """
insert into public.person (workspace_id, client_id, side, display_name, first_seen_at, last_seen_at)
values (%(workspace_id)s, %(client_id)s, %(side)s, %(name)s, %(seen_at)s, %(seen_at)s)
returning id
"""

_INSERT_IDENTITY = """
insert into public.person_identity (workspace_id, person_id, kind, value)
values (%(workspace_id)s, %(person_id)s, 'email', %(email)s)
on conflict (workspace_id, kind, value) do nothing
returning person_id
"""

_DELETE_ORPHAN = (
    "delete from public.person where workspace_id = %(workspace_id)s and id = %(person_id)s"
)

_UPSERT_RECORD = """
insert into public.source_record (
  id, workspace_id, connection_id, provider, external_id, kind, client_id, policy_at_ingest,
  thread_key, occurred_at, author_person_id, direction, participants, word_count, metadata,
  body_clean, raw_object_uri, deep_link, content_hash, processing_state, vault_user_id)
values (
  %(record_id)s, %(workspace_id)s, %(connection_id)s, %(provider)s, %(external_id)s,
  %(kind)s, %(client_id)s,
  %(policy)s, %(thread_key)s, %(occurred_at)s, %(author_person_id)s, %(direction)s,
  %(participants)s, %(word_count)s, %(metadata)s, %(body_clean)s, %(raw_object_uri)s,
  %(deep_link)s, %(content_hash)s, %(processing_state)s,
  -- The recipient is the mailbox owner while they are still a member (or an active contributor).
  (select coalesce(case when exists (select 1 from public.membership m
                                     where m.workspace_id = c.workspace_id
                                       and m.user_id = c.owner_user_id)
                        then c.owner_user_id end,
                   case when contributor.status = 'active'
                                       then (to_jsonb(contributor)->>'user_id')::uuid end)
     from public.connection c
     left join public.contributor contributor
       on contributor.workspace_id = c.workspace_id and contributor.id = c.owner_contributor_id
    where c.workspace_id = %(workspace_id)s and c.id = %(connection_id)s and c.mode = 'user'))
on conflict (connection_id, external_id) do update set
  client_id = excluded.client_id,
  policy_at_ingest = excluded.policy_at_ingest,
  thread_key = excluded.thread_key,
  occurred_at = excluded.occurred_at,
  author_person_id = excluded.author_person_id,
  direction = excluded.direction,
  participants = excluded.participants,
  word_count = excluded.word_count,
  metadata = excluded.metadata,
  -- Keep historical purge protection and an unchanged explicitly rehydrated body.
  body_clean = case when public.source_record.body_purged_at is null
                    then excluded.body_clean
                    when excluded.content_hash
                         is not distinct from public.source_record.content_hash
                    then public.source_record.body_clean end,
  raw_object_uri = excluded.raw_object_uri,
  deep_link = excluded.deep_link,
  content_hash = excluded.content_hash,
  processing_attempts = case
    when excluded.content_hash is distinct from public.source_record.content_hash then 0
    else public.source_record.processing_attempts end,
  vault_user_id = coalesce(public.source_record.vault_user_id, excluded.vault_user_id),
  vault_delivered_at = case
    when excluded.content_hash is distinct from public.source_record.content_hash then null
    else public.source_record.vault_delivered_at end,
  -- New or changed text is processed (again); an unchanged re-sync keeps its state.
  processing_state = case
    when excluded.processing_state = 'pending'
         and public.source_record.body_purged_at is null
         and excluded.content_hash is distinct from public.source_record.content_hash
    then 'pending'::public.processing_state
    when excluded.processing_state = 'not_applicable'
         and public.source_record.processing_state = 'pending'
    then 'not_applicable'::public.processing_state
    else public.source_record.processing_state end
where public.source_record.workspace_id = %(workspace_id)s
returning id, (xmax = 0) as inserted
"""

_UPSERT_EVENT = """
insert into public.calendar_event (
  workspace_id, source_record_id, client_id, starts_at, duration_min, status, attendee_count)
values (%(workspace_id)s, %(record_id)s, %(client_id)s, %(starts_at)s, %(duration_min)s,
        %(status)s, %(attendee_count)s)
on conflict (source_record_id) do update set
  client_id = excluded.client_id, starts_at = excluded.starts_at,
  duration_min = excluded.duration_min, status = excluded.status,
  attendee_count = excluded.attendee_count
where public.calendar_event.workspace_id = %(workspace_id)s
"""

# An upcoming meeting the organizer deleted: keep it, as a cancellation (a relationship signal).
_CANCEL_UPCOMING_EVENT = """
update public.calendar_event e
   set status = 'cancelled'
  from public.source_record r
 where r.workspace_id = %(workspace_id)s and r.connection_id = %(connection_id)s
   and r.external_id = %(external_id)s
   and e.workspace_id = r.workspace_id and e.source_record_id = r.id
   and e.starts_at >= %(after)s and e.starts_at < %(before)s and e.status <> 'cancelled'
"""

# Records that must disappear when a provider deletes them or policy no longer allows them.
_DELETE_RECORD = """
delete from public.source_record
 where workspace_id = %(workspace_id)s and connection_id = %(connection_id)s
   and external_id = %(external_id)s
"""


class RecordRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def upsert_person(
        self, *, workspace_id: WorkspaceId, person: PersonInput, seen_at: datetime
    ) -> UUID:
        params: dict[str, Any] = {
            "workspace_id": workspace_id,
            "email": person.email.lower(),
            "name": (person.name or "").strip()[:200] or None,
            "side": person.side,
            "client_id": person.client_id,
            "seen_at": seen_at,
        }
        with self._pool.connection() as conn:
            found = conn.execute(_FIND_PERSON, params).fetchone()
            if found is None:
                person_id = conn.execute(_INSERT_PERSON, params).fetchone()["id"]  # type: ignore[index]
                claimed = conn.execute(
                    _INSERT_IDENTITY, {**params, "person_id": person_id}
                ).fetchone()
                if claimed is not None:
                    return UUID(str(person_id))
                # Lost a race: another writer created the identity first. Use theirs.
                conn.execute(_DELETE_ORPHAN, {**params, "person_id": person_id})
                found = conn.execute(_FIND_PERSON, params).fetchone()
                if found is None:  # pragma: no cover - identity exists after the conflict
                    raise RuntimeError("person identity vanished")
            person_id = found["person_id"]
            conn.execute(_TOUCH_PERSON, {**params, "person_id": person_id})
        return UUID(str(person_id))

    def set_client_title(
        self,
        *,
        workspace_id: WorkspaceId,
        client_id: UUID,
        person_id: UUID,
        title: str,
        decision_maker: bool,
        observed_at: datetime,
    ) -> None:
        """Keep only a validated title, never the signature it came from.

        The title is also stored as a dated observation (P2b D5), so facts as of an earlier date
        do not see a role that was only signed later.
        """
        params = {
            "workspace_id": workspace_id,
            "client_id": client_id,
            "person_id": person_id,
            "title": title,
            "decision": decision_maker,
            "observed_at": observed_at,
        }
        with self._pool.connection() as conn:
            conn.execute(
                """update public.person set role_title = %(title)s,
                      is_decision_maker = is_decision_maker or %(decision)s
                    where workspace_id = %(workspace_id)s and client_id = %(client_id)s
                      and id = %(person_id)s and side = 'client'
                      and (role_title is distinct from %(title)s or
                           (not is_decision_maker and %(decision)s))""",
                params,
            )
            conn.execute(_INSERT_OBSERVATION, params)

    def upsert_record(
        self,
        *,
        workspace_id: WorkspaceId,
        record: RecordInput,
        author_person_id: UUID | None,
        participants: list[UUID],
        event: EventInput | None = None,
    ) -> tuple[UUID, bool]:
        params: dict[str, Any] = {
            "record_id": uuid4(),
            "workspace_id": workspace_id,
            "connection_id": record.connection_id,
            "provider": record.provider,
            "external_id": record.external_id,
            "kind": record.kind,
            "client_id": record.client_id,
            "policy": record.policy,
            "thread_key": record.thread_key,
            "occurred_at": record.occurred_at,
            "author_person_id": author_person_id,
            "direction": record.direction,
            "participants": participants,
            "word_count": record.word_count,
            "metadata": Jsonb(record.metadata),
            "body_clean": record.body_clean,
            "raw_object_uri": record.raw_object_uri,
            "deep_link": record.deep_link,
            "content_hash": record.content_hash,
            # Only text the model may read is queued for extraction (P2 D7).
            "processing_state": (
                "pending"
                if record.kind == "email" and record.policy == "full" and record.body_clean
                else "not_applicable"
            ),
        }
        with self._pool.connection() as conn:
            if provider_for(self._pool) is not None:
                # ponytail: serialize local upserts per connection to know the final row id
                # before encryption; use per-external-id locks only if ingest throughput needs it.
                conn.execute(
                    "select id from public.connection where workspace_id = %(workspace_id)s "
                    "and id = %(connection_id)s for update",
                    params,
                )
                existing = conn.execute(
                    "select id from public.source_record where workspace_id = %(workspace_id)s "
                    "and connection_id = %(connection_id)s and external_id = %(external_id)s",
                    params,
                ).fetchone()
                if existing is not None:
                    params["record_id"] = existing["id"]
                params["body_clean"] = write_body(
                    self._pool, workspace_id, params["record_id"], record.body_clean
                )
                params["metadata"] = Jsonb(
                    {
                        key: write_field(self._pool, workspace_id, params["record_id"], key, value)
                        if key in ENCRYPTED_FIELDS and isinstance(value, str)
                        else value
                        for key, value in record.metadata.items()
                    }
                )
            row = conn.execute(_UPSERT_RECORD, params).fetchone()
            if row is None:  # pragma: no cover - guarded by the composite foreign key
                raise RuntimeError("record upsert affected no row")
            if event is not None:
                conn.execute(
                    _UPSERT_EVENT,
                    {
                        "workspace_id": workspace_id,
                        "record_id": row["id"],
                        "client_id": record.client_id,
                        "starts_at": event.starts_at,
                        "duration_min": event.duration_min,
                        "status": event.status,
                        "attendee_count": event.attendee_count,
                    },
                )
        return UUID(str(row["id"])), bool(row["inserted"])

    def delete_record(
        self, *, workspace_id: WorkspaceId, connection_id: UUID, external_id: str
    ) -> bool:
        with self._pool.connection() as conn:
            result = conn.execute(
                _DELETE_RECORD,
                {
                    "workspace_id": workspace_id,
                    "connection_id": connection_id,
                    "external_id": external_id,
                },
            )
        return result.rowcount > 0

    def cancel_upcoming_event(
        self,
        *,
        workspace_id: WorkspaceId,
        connection_id: UUID,
        external_id: str,
        after: datetime,
        before: datetime,
    ) -> bool:
        """Marks a stored event starting in [after, before) as cancelled; False if none matched."""
        with self._pool.connection() as conn:
            result = conn.execute(
                _CANCEL_UPCOMING_EVENT,
                {
                    "workspace_id": workspace_id,
                    "connection_id": connection_id,
                    "external_id": external_id,
                    "after": after,
                    "before": before,
                },
            )
        return result.rowcount > 0
