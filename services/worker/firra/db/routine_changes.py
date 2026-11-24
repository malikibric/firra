"""Read-only, workspace-scoped change history for the morning scan; codes and ids only."""

from datetime import date, timedelta
from typing import Any

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId


class RoutineChangesRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self.pool = pool

    def since(self, *, workspace_id: WorkspaceId, as_of: date) -> date:
        with self.pool.connection() as conn:
            row = conn.execute(
                """select max(rr.scheduled_for) as day from public.routine_run rr
                join public.routine r on r.workspace_id = rr.workspace_id and r.id = rr.routine_id
                where rr.workspace_id = %(workspace_id)s and r.kind = 'morning_scan'
                  and rr.status = 'succeeded' and rr.scheduled_for < %(as_of)s""",
                {"workspace_id": workspace_id, "as_of": as_of},
            ).fetchone()
        previous = row["day"] if row and row["day"] else as_of - timedelta(days=1)
        return max(previous, as_of - timedelta(days=7))

    def assessments(
        self, *, workspace_id: WorkspaceId, since: date, as_of: date
    ) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute(
                """with history as (
                  select a.client_id, a.as_of, a.risk_state::text, a.previous_risk_state::text,
                    a.expansion_state::text, lag(a.expansion_state::text)
                      over (partition by a.client_id order by a.as_of) as previous_expansion,
                    case when c.policy = 'full' then array(select e.evidence_id
                      from public.assessment_evidence e where e.workspace_id = a.workspace_id
                      and e.assessment_id = a.id order by e.evidence_id)
                      else '{}'::uuid[] end as evidence_ids
                  from public.account_assessment a join public.client c
                    on c.workspace_id = a.workspace_id and c.id = a.client_id
                  where a.workspace_id = %(workspace_id)s and a.run_kind = 'live'
                    and c.status = 'active' and c.policy <> 'excluded' and a.as_of <= %(as_of)s
                ) select * from history where as_of > %(since)s order by client_id, as_of""",
                {"workspace_id": workspace_id, "since": since, "as_of": as_of},
            ).fetchall()

    def signals(
        self, *, workspace_id: WorkspaceId, since: date, as_of: date
    ) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute(
                """with history as (
                  select e.client_id, e.signal_id, e.id, e.as_of, e.created_at, e.change::text,
                    e.severity::text, lag(e.severity::text) over (partition by e.signal_id
                      order by e.as_of, e.created_at, e.id) as previous_severity,
                    case when e.change <> 'resolved' and c.policy = 'full' and s.basis = 'evidence'
                      then array(select l.evidence_id from public.signal_evidence l
                        where l.workspace_id = e.workspace_id and l.signal_id = e.signal_id
                        order by l.evidence_id) else '{}'::uuid[] end as evidence_ids
                  from public.signal_event e join public.client c
                    on c.workspace_id = e.workspace_id and c.id = e.client_id
                  join public.signal s on s.workspace_id = e.workspace_id and s.id = e.signal_id
                  where e.workspace_id = %(workspace_id)s and c.status = 'active'
                    and c.policy <> 'excluded' and (e.as_of at time zone 'UTC')::date <= %(as_of)s
                ) select * from history where (as_of at time zone 'UTC')::date > %(since)s
                  order by as_of, created_at, id""",
                {"workspace_id": workspace_id, "since": since, "as_of": as_of},
            ).fetchall()

    def deadlines(self, *, workspace_id: WorkspaceId) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute(
                """select id as client_id, renewal_date - notice_period_days as deadline
                from public.client where workspace_id = %(workspace_id)s and status = 'active'
                  and policy <> 'excluded' and renewal_date is not null
                  and notice_period_days is not null order by id""",
                {"workspace_id": workspace_id},
            ).fetchall()
