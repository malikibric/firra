"""workspace_key storage (wrapped DEKs). Scoped by workspace_id."""

from typing import Protocol

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId


class WorkspaceKeyStore(Protocol):
    def get(self, *, workspace_id: WorkspaceId) -> bytes | None: ...

    def insert_if_absent(
        self, *, workspace_id: WorkspaceId, wrapped_dek: bytes, kek_name: str
    ) -> bytes: ...


_GET = "select wrapped_dek from public.workspace_key where workspace_id = %(workspace_id)s"
_INSERT = """
insert into public.workspace_key (workspace_id, wrapped_dek, kek_name)
values (%(workspace_id)s, %(wrapped_dek)s, %(kek_name)s)
on conflict (workspace_id) do nothing
"""


class PgWorkspaceKeyStore:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def get(self, *, workspace_id: WorkspaceId) -> bytes | None:
        with self._pool.connection() as conn:
            row = conn.execute(_GET, {"workspace_id": workspace_id}).fetchone()
        return None if row is None else bytes(row["wrapped_dek"])

    def insert_if_absent(
        self, *, workspace_id: WorkspaceId, wrapped_dek: bytes, kek_name: str
    ) -> bytes:
        with self._pool.connection() as conn:
            conn.execute(
                _INSERT,
                {"workspace_id": workspace_id, "wrapped_dek": wrapped_dek, "kek_name": kek_name},
            )
            row = conn.execute(_GET, {"workspace_id": workspace_id}).fetchone()
        if row is None:  # pragma: no cover - the insert above guarantees a row
            raise RuntimeError("workspace key missing after insert")
        return bytes(row["wrapped_dek"])
