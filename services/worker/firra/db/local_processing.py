"""Conversation packets and counts: full, pending rows of the owner's workspace only.

Body decryption belongs to this repository. Shared AiProcessingRepo writes recheck the original
record snapshot and current full policy under transaction locks.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from firra.db.ai_processing import _PACKET_PENDING, AiProcessingRepo, PacketRecord
from firra.db.processing import PendingRecord
from firra.db.types import WorkspaceId
from firra.local.bodycrypt import read_body, read_field
from firra_contracts import TaskEnvelope

_ELIGIBLE = """and r.policy_at_ingest = 'full' and c.policy = 'full'
    and r.kind in ('email', 'chat') and r.body_clean is not null"""
_QUERY = _PACKET_PENDING.replace(
    "and r.processing_state = 'pending'", "and r.processing_state = 'pending' " + _ELIGIBLE
)
_COUNT = """select count(*) as n from public.source_record r
    join public.client c on c.workspace_id = %(workspace_id)s and c.id = r.client_id
    where r.workspace_id = %(workspace_id)s and r.processing_state = 'pending'
      and r.policy_at_ingest = 'full' and c.policy = 'full'
      and r.kind in ('email', 'chat') and r.body_clean is not null
"""


class ConversationRepo(AiProcessingRepo):
    def pending(self, *, workspace_id: WorkspaceId, limit: int) -> list[PendingRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(_QUERY, {"workspace_id": workspace_id, "limit": limit}).fetchall()
        records: list[PendingRecord] = []
        for row in rows:
            values: dict[str, Any] = {f.name: row[f.name] for f in fields(PacketRecord)}
            values["body_clean"] = read_body(self._pool, workspace_id, row["id"], row["body_clean"])
            values["subject"] = read_field(
                self._pool, workspace_id, row["id"], "subject", values["subject"]
            )
            values["participant_emails"] = frozenset(values["participant_emails"])
            records.append(PacketRecord(**values))
        return records

    def remaining(self, *, workspace_id: WorkspaceId, record_ids: list[UUID] | None = None) -> int:
        query = _COUNT + (" and r.id = any(%(record_ids)s)" if record_ids is not None else "")
        with self._write_connection() as conn:
            row = conn.execute(  # nosemgrep
                query, {"workspace_id": workspace_id, "record_ids": record_ids}
            ).fetchone()
        return int(row["n"]) if row else 0

    def still_eligible(self, *, workspace_id: WorkspaceId, record_ids: list[UUID]) -> bool:
        query = _COUNT + " and r.id = any(%(record_ids)s)"
        with self._pool.connection() as conn:
            row = conn.execute(  # nosemgrep
                query, {"workspace_id": workspace_id, "record_ids": record_ids}
            ).fetchone()
        return row is not None and int(row["n"]) == len(record_ids)

    @contextmanager
    def submission(self) -> Iterator[None]:
        """All records in a submitted lease commit together; a DB failure can safely retry."""
        with self._pool.connection() as conn, conn.transaction():
            previous = self._record_conn
            self._record_conn = conn
            try:
                yield
            finally:
                self._record_conn = previous

    def request_followup(self, *, workspace_id: WorkspaceId, lease_id: UUID) -> None:
        """Same transaction as the packet's writes; only one job for this completed lease."""
        with self._write_connection() as conn:
            conn.execute(
                "insert into public.job (workspace_id,type,idempotency_key,payload) "
                "values (%(workspace_id)s,'memory.rebuild',%(key)s,%(payload)s) "
                "on conflict (workspace_id,idempotency_key) do nothing",
                {
                    "workspace_id": workspace_id,
                    "key": f"memory:conversation:{lease_id}",
                    "payload": Jsonb({"trigger": "processing", "as_of": None}),
                },
            )

    def followup(self, *, workspace_id: WorkspaceId, lease_id: UUID) -> TaskEnvelope | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select id, payload, idempotency_key from public.job "
                "where workspace_id = %(workspace_id)s and idempotency_key = %(key)s "
                "and type = 'memory.rebuild' and status in ('queued', 'running')",
                {"workspace_id": workspace_id, "key": f"memory:conversation:{lease_id}"},
            ).fetchone()
        if row is None:
            return None
        return TaskEnvelope(
            workspace_id=workspace_id,
            job_id=row["id"],
            idempotency_key=row["idempotency_key"],
            type="memory.rebuild",
            payload=row["payload"],
        )
