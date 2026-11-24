"""Local packet reads and durable retry accounting, always workspace-scoped.

Reuse the existing processing repository for policy-locked writes and progress
transactions. Retry updates join that same transaction and reject stale snapshots.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, fields
from typing import Any

from psycopg import Connection
from psycopg.rows import DictRow

from firra.db.processing import _PENDING, ItemRow, PendingRecord, ProcessingRepo
from firra.db.types import WorkspaceId
from firra.local.bodycrypt import read_body, read_field


@dataclass(frozen=True, slots=True)
class PacketRecord(PendingRecord):
    channel: str | None = None
    author_name: str | None = None


# Keep exactly the existing facts query, changing only local ordering and Slack headers.
_PACKET_PENDING = _PENDING.replace(
    "select r.id,",
    "select r.metadata->>'channel' as channel, r.metadata->>'author_name' as author_name, r.id,",
).replace(
    "order by r.occurred_at, r.id",
    "order by (c.status = 'active') desc nulls last, r.occurred_at desc, r.id",
)

_ATTEMPT = """
update public.source_record
   set processing_attempts = least(processing_attempts + 1, 20),
       processing_state = case when processing_attempts + 1 >= 3
                              then 'error'::public.processing_state
                              else 'pending'::public.processing_state end,
       processed_at = case when processing_attempts + 1 >= 3 then now() else processed_at end
 where workspace_id = %(workspace_id)s and id = %(id)s and processing_state = 'pending'
   and processing_version = %(version)s
   and content_hash is not distinct from %(content_hash)s
returning processing_state::text as state
"""

_LOCK_SNAPSHOT = """
select id from public.source_record
 where workspace_id = %(workspace_id)s and id = %(id)s
   and processing_state = 'pending' and policy_at_ingest = 'full'
   and processing_version = %(version)s
   and content_hash is not distinct from %(content_hash)s
 for update
"""

_LOCK_POLICY = """
select policy::text as policy from public.client
 where workspace_id = %(workspace_id)s and id = %(client_id)s for share
"""


class AiProcessingRepo(ProcessingRepo):
    @contextmanager
    def _write_connection(self) -> Iterator[Connection[DictRow]]:
        if self._record_conn is not None:
            with self._record_conn.transaction():
                yield self._record_conn
        else:
            with super()._write_connection() as conn:
                yield conn

    def pending(self, *, workspace_id: WorkspaceId, limit: int) -> list[PendingRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                _PACKET_PENDING, {"workspace_id": workspace_id, "limit": limit}
            ).fetchall()
        records: list[PendingRecord] = []
        for row in rows:
            values: dict[str, Any] = {field.name: row[field.name] for field in fields(PacketRecord)}
            values["participant_emails"] = frozenset(values["participant_emails"])
            values["body_clean"] = read_body(self._pool, workspace_id, row["id"], row["body_clean"])
            values["subject"] = read_field(
                self._pool, workspace_id, row["id"], "subject", values["subject"]
            )
            records.append(PacketRecord(**values))
        return records

    def attempt(self, *, workspace_id: WorkspaceId, record: PendingRecord) -> str | None:
        """Atomic increment and park; None means the snapshot changed meanwhile."""
        with self._write_connection() as conn:
            row = conn.execute(
                _ATTEMPT,
                {
                    "workspace_id": workspace_id,
                    "id": record.id,
                    "version": record.processing_version,
                    "content_hash": record.content_hash,
                },
            ).fetchone()
        return str(row["state"]) if row else None

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
        # Ingest can replace content without increasing processing_version. Hold the same
        # client-before-record lock order as policy updates, and validate the content snapshot.
        params: dict[str, Any] = {
            "workspace_id": workspace_id,
            "id": record.id,
            "client_id": record.client_id,
            "version": record.processing_version,
            "content_hash": record.content_hash,
        }
        with self._write_connection() as conn:
            policy = conn.execute(_LOCK_POLICY, params).fetchone()
            if policy is None or policy["policy"] != "full":
                return False
            if conn.execute(_LOCK_SNAPSHOT, params).fetchone() is None:
                return False
            # Ensure the base save uses this connection even outside record_progress().
            previous = self._record_conn
            self._record_conn = conn
            try:
                return super().save(
                    workspace_id=workspace_id,
                    record=record,
                    cleaned_body=cleaned_body,
                    items=items,
                    model=model,
                    prompt_version=prompt_version,
                )
            finally:
                self._record_conn = previous
