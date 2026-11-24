"""Connection rows as the worker needs them. Scoped by (workspace_id, id)."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId


@dataclass(frozen=True, slots=True)
class ConnectionRow:
    id: UUID
    workspace_id: WorkspaceId
    provider: str
    mode: str
    status: str
    account_label: str
    external_account_id: str | None
    token_ciphertext: str | None
    cursor: dict[str, Any]
    backfill_months: int


_GET = """
select id, workspace_id, provider::text, mode::text, status::text, account_label,
       external_account_id, token_ciphertext, cursor, backfill_months
  from public.connection
 where workspace_id = %(workspace_id)s and id = %(connection_id)s
"""

_GET_LOCAL = _GET.replace(
    "external_account_id, token_ciphertext,", "external_account_id, null::text as token_ciphertext,"
)

_SYNCED = """
update public.connection
   set cursor = cursor || %(cursor)s, last_synced_at = now(), last_error_class = null,
       status = case when status = 'error' then 'active'::public.connection_status else status end
 where workspace_id = %(workspace_id)s and id = %(connection_id)s and status <> 'revoked'
"""

_FAILED = """
update public.connection
   set status = %(status)s::public.connection_status, last_error_class = %(error_class)s
 where workspace_id = %(workspace_id)s and id = %(connection_id)s and status <> 'revoked'
"""


_STORE_CIPHERTEXT = """
update public.connection
   set token_ciphertext = %(ciphertext)s
 where workspace_id = %(workspace_id)s and id = %(connection_id)s and status <> 'revoked'
"""


class ConnectionRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]], *, local: bool = False) -> None:
        self._pool = pool
        self._get = _GET_LOCAL if local else _GET

    def get(self, *, workspace_id: WorkspaceId, connection_id: UUID) -> ConnectionRow | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                self._get, {"workspace_id": workspace_id, "connection_id": connection_id}
            ).fetchone()
        if row is None:
            return None
        return ConnectionRow(
            id=row["id"],
            workspace_id=WorkspaceId(row["workspace_id"]),
            provider=row["provider"],
            mode=row["mode"],
            status=row["status"],
            account_label=row["account_label"],
            external_account_id=row["external_account_id"],
            token_ciphertext=row["token_ciphertext"],
            cursor=dict(row["cursor"]),
            backfill_months=row["backfill_months"],
        )

    def mark_synced(
        self, *, workspace_id: WorkspaceId, connection_id: UUID, cursor: dict[str, Any]
    ) -> None:
        """Merges ``cursor`` into the stored cursor (top-level keys are replaced)."""
        with self._pool.connection() as conn:
            conn.execute(
                _SYNCED,
                {
                    "workspace_id": workspace_id,
                    "connection_id": connection_id,
                    "cursor": Jsonb(cursor),
                },
            )

    def mark_failed(
        self, *, workspace_id: WorkspaceId, connection_id: UUID, error_class: str, revoked: bool
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                _FAILED,
                {
                    "workspace_id": workspace_id,
                    "connection_id": connection_id,
                    "error_class": error_class,
                    "status": "revoked" if revoked else "error",
                },
            )

    def save_token(
        self, *, workspace_id: WorkspaceId, connection_id: UUID, ciphertext: str
    ) -> None:
        """Stores a rotated provider token (already encrypted for this connection)."""
        with self._pool.connection() as conn:
            conn.execute(
                _STORE_CIPHERTEXT,
                {
                    "workspace_id": workspace_id,
                    "connection_id": connection_id,
                    "ciphertext": ciphertext,
                },
            )
