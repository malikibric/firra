"""Record processing state, extraction and evidence rows (P2 D5-D7).

Every statement is scoped by workspace_id, except ``workspaces_with_pending`` (sweep).
"""

import hashlib
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg import Connection
from psycopg.errors import CheckViolation
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.jobs import ClaimedJob
from firra.db.types import WorkspaceId, cross_tenant_maintenance
from firra.local.bodycrypt import read_body, read_field, write_body

PROCESS_JOB = "process.records"
# Raised by the evidence trigger when the client stopped being full-policy meanwhile (D15).
POLICY_CHANGED = "evidence requires a full-policy client"
FinalState = Literal["done", "error", "skipped_purged", "not_applicable"]


@dataclass(frozen=True, slots=True)
class PendingRecord:
    id: UUID
    client_id: UUID | None
    kind: str
    thread_key: str | None
    occurred_at: datetime
    direction: Literal["in", "out", "internal"]
    body_clean: str | None
    subject: str | None
    deep_link: str | None
    content_hash: bytes | None
    processing_version: int
    policy: str
    client_policy: str | None
    author_email: str | None
    author_side: Literal["client", "firm"] | None
    participant_emails: frozenset[str]


@dataclass(frozen=True, slots=True)
class ItemRow:
    kind: str
    payload: dict[str, Any]
    quote: str
    locator: dict[str, int]


_PENDING = """
select r.id, r.client_id, r.kind::text as kind, r.thread_key, r.occurred_at,
       r.direction::text as direction, r.body_clean, r.metadata->>'subject' as subject,
       r.deep_link, r.content_hash, r.processing_version, r.policy_at_ingest::text as policy,
       c.policy::text as client_policy,
       (select pi.value from public.person_identity pi
         where pi.workspace_id = r.workspace_id and pi.person_id = r.author_person_id
           and pi.kind = 'email' order by pi.value limit 1) as author_email,
       (select p.side::text from public.person p
         where p.workspace_id = r.workspace_id and p.id = r.author_person_id) as author_side,
       coalesce((select array_agg(pi.value) from public.person_identity pi
                  where pi.workspace_id = r.workspace_id and pi.person_id = any(r.participants)
                    and pi.kind = 'email'), '{}') as participant_emails
  from public.source_record r
  left join public.client c on c.workspace_id = r.workspace_id and c.id = r.client_id
 where r.workspace_id = %(workspace_id)s and r.processing_state = 'pending'
 order by r.occurred_at, r.id
 limit %(limit)s
"""

_CONTEXT = """
select id, body_clean from public.source_record
 where workspace_id = %(workspace_id)s and client_id = %(client_id)s
   and thread_key = %(thread_key)s and id <> %(id)s and occurred_at <= %(occurred_at)s
   and body_clean is not null
 order by occurred_at desc
 limit %(limit)s
"""

_DUPLICATE_DONE = """
select exists (
  select 1 from public.source_record
   where workspace_id = %(workspace_id)s and thread_key = %(thread_key)s
     and content_hash = %(content_hash)s and id <> %(id)s and processing_state = 'done'
) as duplicate
"""

_MARK = """
update public.source_record
   set processing_state = %(state)s, processed_at = now()
 where workspace_id = %(workspace_id)s and id = %(id)s and processing_state = 'pending'
"""

# Claims the record for this result: still pending and not re-ingested since it was read.
# Taken first: set_client_policy locks the client row and then the client's records, so a save
# that locked a record before waiting on the client could deadlock with it. Holding the client
# row in share mode also makes a policy change wait until the save commits (or skip it here).
_LOCK_CLIENT = """
select policy::text as policy from public.client
 where workspace_id = %(workspace_id)s and id = %(client_id)s
   for share
"""

_FINISH = """
update public.source_record
   set processing_state = 'done', processed_at = now(),
       processing_version = processing_version + 1,
       body_clean = coalesce(%(body)s, body_clean),
       content_hash = coalesce(%(content_hash)s, content_hash)
 where workspace_id = %(workspace_id)s and id = %(id)s and processing_state = 'pending'
   and processing_version = %(version)s and body_clean is not null
returning processing_version
"""

_DELETE_EXTRACTIONS = """
delete from public.extraction where workspace_id = %(workspace_id)s and source_record_id = %(id)s
"""
# Evidence is kept by quote: derived rows (commitments, signals, cards) cite it by id, so a
# reprocess only removes quotes the new run no longer produced.
_DELETE_STALE_EVIDENCE = """
delete from public.evidence
 where workspace_id = %(workspace_id)s and source_record_id = %(id)s
   and quote <> all(%(quotes)s::text[])
"""

_INSERT_EVIDENCE = """
insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator, deep_link)
values (%(workspace_id)s, %(id)s, %(client_id)s, %(quote)s, %(locator)s, %(deep_link)s)
on conflict (workspace_id, source_record_id, quote)
do update set locator = excluded.locator, deep_link = excluded.deep_link
returning id
"""

_INSERT_EXTRACTION = """
insert into public.extraction (workspace_id, source_record_id, client_id, kind, payload, model,
                               prompt_version, processing_version, item_index)
values (%(workspace_id)s, %(id)s, %(client_id)s, %(kind)s, %(payload)s, %(model)s,
        %(prompt_version)s, %(version)s, %(item_index)s)
returning id
"""

_INSERT_LINK = """
insert into public.extraction_evidence (workspace_id, extraction_id, evidence_id, client_id)
values (%(workspace_id)s, %(extraction_id)s, %(evidence_id)s, %(client_id)s)
"""

_HAS_PENDING = """
select exists (
  select 1 from public.source_record
   where workspace_id = %(workspace_id)s and processing_state = 'pending'
) as pending
"""

_ACTIVE_JOB = """
select exists (
  select 1 from public.job
   where workspace_id = %(workspace_id)s and type = %(job_type)s
     and status in ('queued', 'running')
) as active
"""


_RECORD_PROGRESS = """
update public.job set progress = progress || %(progress)s
 where workspace_id = %(workspace_id)s and id = %(job_id)s
   and type = 'process.records' and status = 'running' and attempts = %(attempt)s
returning id
"""


class ProcessingRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool
        self._record_stack: ExitStack | None = None
        self._record_conn: Connection[DictRow] | None = None

    @contextmanager
    def record_progress(
        self, *, job: ClaimedJob, progress: Callable[[], dict[str, Any]]
    ) -> Iterator[None]:
        """Commit a record's writes and counters together, or roll both back.

        Acquire a connection only on the first write, after the paid model call.
        The attempt guard prevents a superseded delivery from overwriting counters.
        """
        if self._record_stack is not None:
            raise RuntimeError("record progress transaction already active")
        with ExitStack() as stack:
            self._record_stack = stack
            try:
                yield
                with self._write_connection() as conn:
                    updated = conn.execute(
                        _RECORD_PROGRESS,
                        {
                            "workspace_id": job.workspace_id,
                            "job_id": job.id,
                            "attempt": job.attempts,
                            "progress": Jsonb(progress()),
                        },
                    ).fetchone()
                    if updated is None:
                        raise RuntimeError("processing job ownership lost")
            finally:
                self._record_stack = None
                self._record_conn = None

    @contextmanager
    def _write_connection(self) -> Iterator[Connection[DictRow]]:
        if self._record_stack is None:
            with self._pool.connection() as conn:
                yield conn
            return
        if self._record_conn is None:
            self._record_conn = self._record_stack.enter_context(self._pool.connection())
            self._record_stack.enter_context(self._record_conn.transaction())
        # save() handles a policy CheckViolation: a savepoint keeps the outer
        # transaction usable so changed counters can still commit.
        with self._record_conn.transaction():
            yield self._record_conn

    def pending(self, *, workspace_id: WorkspaceId, limit: int) -> list[PendingRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(_PENDING, {"workspace_id": workspace_id, "limit": limit}).fetchall()
        return [
            PendingRecord(
                id=r["id"],
                client_id=r["client_id"],
                kind=r["kind"],
                thread_key=r["thread_key"],
                occurred_at=r["occurred_at"],
                direction=r["direction"],
                body_clean=read_body(self._pool, workspace_id, r["id"], r["body_clean"]),
                subject=read_field(self._pool, workspace_id, r["id"], "subject", r["subject"]),
                deep_link=r["deep_link"],
                content_hash=r["content_hash"],
                processing_version=r["processing_version"],
                policy=r["policy"],
                client_policy=r["client_policy"],
                author_email=r["author_email"],
                author_side=r["author_side"],
                participant_emails=frozenset(r["participant_emails"]),
            )
            for r in rows
        ]

    def thread_context(
        self, *, workspace_id: WorkspaceId, record: PendingRecord, limit: int
    ) -> list[str]:
        """Up to ``limit`` earlier bodies of the record's thread, oldest first."""
        if record.thread_key is None or record.client_id is None or limit <= 0:
            return []
        with self._pool.connection() as conn:
            rows = conn.execute(
                _CONTEXT,
                {
                    "workspace_id": workspace_id,
                    "client_id": record.client_id,
                    "thread_key": record.thread_key,
                    "id": record.id,
                    "occurred_at": record.occurred_at,
                    "limit": limit,
                },
            ).fetchall()
        return [
            str(read_body(self._pool, workspace_id, r["id"], r["body_clean"]))
            for r in reversed(rows)
        ]

    def is_duplicate(
        self, *, workspace_id: WorkspaceId, record: PendingRecord, content_hash: bytes
    ) -> bool:
        """Same cleaned content already processed in the same thread (re-sends, copies)."""
        if record.thread_key is None:
            return False
        with self._pool.connection() as conn:
            row = conn.execute(
                _DUPLICATE_DONE,
                {
                    "workspace_id": workspace_id,
                    "thread_key": record.thread_key,
                    "content_hash": content_hash,
                    "id": record.id,
                },
            ).fetchone()
        return bool(row and row["duplicate"])

    def mark(self, *, workspace_id: WorkspaceId, record_id: UUID, state: FinalState) -> None:
        with self._write_connection() as conn:
            conn.execute(_MARK, {"workspace_id": workspace_id, "id": record_id, "state": state})

    def save(
        self,
        *,
        workspace_id: WorkspaceId,
        record: PendingRecord,
        cleaned_body: str | None,
        items: Sequence[ItemRow],
        model: str,
        prompt_version: str,
    ) -> bool:
        """Stores the record's result; False when it changed meanwhile, incl. a policy change."""
        try:
            return self._save(
                workspace_id=workspace_id,
                record=record,
                cleaned_body=cleaned_body,
                items=items,
                model=model,
                prompt_version=prompt_version,
            )
        except CheckViolation as exc:
            if exc.diag.message_primary != POLICY_CHANGED:
                raise
            return False  # rolled back with the connection: nothing was written

    def _save(
        self,
        *,
        workspace_id: WorkspaceId,
        record: PendingRecord,
        cleaned_body: str | None,
        items: Sequence[ItemRow],
        model: str,
        prompt_version: str,
    ) -> bool:
        """Replaces the record's extractions and marks it done, in one transaction.

        Evidence rows of quotes that are still present keep their ids (derived rows cite them).

        ``cleaned_body`` replaces the stored body when re-cleaning changed it (records ingested
        before cleaning existed). False when the record changed meanwhile (nothing is written).
        """
        base = {"workspace_id": workspace_id, "id": record.id, "client_id": record.client_id}
        content_hash = hashlib.sha256(cleaned_body.encode()).digest() if cleaned_body else None
        with self._write_connection() as conn:
            client = conn.execute(_LOCK_CLIENT, base).fetchone()
            if client is None or client["policy"] != "full":
                return False
            finished = conn.execute(
                _FINISH,
                {
                    **base,
                    "version": record.processing_version,
                    "body": write_body(self._pool, workspace_id, record.id, cleaned_body),
                    "content_hash": content_hash,
                },
            ).fetchone()
            if finished is None:
                return False
            conn.execute(_DELETE_EXTRACTIONS, base)
            for index, item in enumerate(items):
                evidence = conn.execute(
                    _INSERT_EVIDENCE,
                    {
                        **base,
                        "quote": item.quote,
                        "locator": Jsonb(item.locator),
                        "deep_link": record.deep_link,
                    },
                ).fetchone()
                extraction = conn.execute(
                    _INSERT_EXTRACTION,
                    {
                        **base,
                        "kind": item.kind,
                        "payload": Jsonb(item.payload),
                        "model": model,
                        "prompt_version": prompt_version,
                        "version": finished["processing_version"],
                        "item_index": index,
                    },
                ).fetchone()
                if evidence is None or extraction is None:  # pragma: no cover - returning id
                    raise RuntimeError("evidence or extraction insert returned no id")
                conn.execute(
                    _INSERT_LINK,
                    {
                        **base,
                        "extraction_id": extraction["id"],
                        "evidence_id": evidence["id"],
                    },
                )
            conn.execute(_DELETE_STALE_EVIDENCE, {**base, "quotes": [item.quote for item in items]})
        return True

    def has_pending(self, *, workspace_id: WorkspaceId) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(_HAS_PENDING, {"workspace_id": workspace_id}).fetchone()
        return bool(row and row["pending"])

    def has_active_job(self, *, workspace_id: WorkspaceId) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(
                _ACTIVE_JOB, {"workspace_id": workspace_id, "job_type": PROCESS_JOB}
            ).fetchone()
        return bool(row and row["active"])


@cross_tenant_maintenance("sweep dispatches processing for every workspace with pending records")
def workspaces_with_pending(pool: ConnectionPool[Connection[DictRow]]) -> list[WorkspaceId]:
    """Workspaces with pending records and no processing job queued or running."""
    query = """
    select distinct r.workspace_id from public.source_record r
     where r.processing_state = 'pending'
       and not exists (select 1 from public.job j
                        where j.workspace_id = r.workspace_id and j.type = %(job_type)s
                          and j.status in ('queued', 'running'))
    """
    with pool.connection() as conn:
        rows = conn.execute(query, {"job_type": PROCESS_JOB}).fetchall()
    return [WorkspaceId(r["workspace_id"]) for r in rows]
