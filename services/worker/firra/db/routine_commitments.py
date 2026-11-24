"""Read-only, workspace-scoped commitments and successful watch history."""

from datetime import date, timedelta
from typing import Any

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId


class RoutineCommitmentsRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self.pool = pool

    def since(self, *, workspace_id: WorkspaceId, as_of: date) -> date:
        with self.pool.connection() as conn:
            row = conn.execute(
                """select max(rr.scheduled_for) as day from public.routine_run rr
                join public.routine r on r.workspace_id = rr.workspace_id and r.id = rr.routine_id
                where rr.workspace_id = %(workspace_id)s and r.kind = 'commitment_watch'
                  and rr.status = 'succeeded' and rr.scheduled_for < %(as_of)s""",
                {"workspace_id": workspace_id, "as_of": as_of},
            ).fetchone()
        previous = row["day"] if row and row["day"] else as_of - timedelta(days=1)
        return max(previous, as_of - timedelta(days=7))

    def commitments(self, *, workspace_id: WorkspaceId, as_of: date) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute(
                """select c.id, c.client_id, c.owner_side::text, c.status::text, c.due_date,
                  array(select e.evidence_id from public.commitment_evidence e
                    where e.workspace_id = c.workspace_id and e.commitment_id = c.id
                    order by e.evidence_id) as evidence_ids
                from public.commitment c join public.client cl
                  on cl.workspace_id = c.workspace_id and cl.id = c.client_id
                where c.workspace_id = %(workspace_id)s and c.closed_at is null
                  and cl.status = 'active' and cl.policy = 'full'
                  and (c.stated_at at time zone 'UTC')::date <= %(as_of)s
                order by c.client_id, c.id""",
                {"workspace_id": workspace_id, "as_of": as_of},
            ).fetchall()
