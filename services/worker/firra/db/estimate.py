"""Content-free, workspace-scoped email counts for the pilot cost estimate."""

from typing import Any

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId

_COUNTS = """
select c.id as client_id, c.status::text as status, count(*) as emails
  from public.source_record r
  join public.client c on c.workspace_id = r.workspace_id and c.id = r.client_id
 where r.workspace_id = %(workspace_id)s and r.kind = 'email'
   and r.policy_at_ingest = 'full' and c.policy = 'full'
   and r.occurred_at >= current_timestamp - make_interval(months => %(months)s)
   and r.occurred_at <= current_timestamp
 group by c.id, c.status
 order by c.id
"""


class EstimateRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def counts(self, *, workspace_id: WorkspaceId, months: int) -> list[dict[str, Any]]:
        if months < 1:
            raise ValueError("months must be positive")
        with self._pool.connection() as conn:
            rows = conn.execute(_COUNTS, {"workspace_id": workspace_id, "months": months})
            return [dict(row) for row in rows.fetchall()]
