"""Workspace-scoped, content-free learning inputs; every read excludes excluded clients."""

from datetime import date

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId
from firra.learning.metrics import AssessmentRow, FeedbackRow, OutcomeRow

_FEEDBACK = """
select f.decision_card_id as card_id, f.client_id, d.kind::text, d.play,
       f.verdict::text, f.action::text
from public.decision_feedback f
join public.decision_card d on d.workspace_id = f.workspace_id
  and d.id = f.decision_card_id and d.client_id = f.client_id
join public.client c on c.workspace_id = f.workspace_id and c.id = f.client_id
where f.workspace_id = %(workspace_id)s and c.policy <> 'excluded'
  and f.updated_at < (%(as_of)s::date + 1)::timestamptz
  and d.created_at < (%(as_of)s::date + 1)::timestamptz
order by f.id
"""
_ASSESSMENTS = """
select a.client_id, a.as_of, a.risk_state::text,
       array(select distinct driver->>'family' from jsonb_array_elements(a.drivers) driver
             where driver->'primary' = 'true'::jsonb and driver->>'family' is not null
             order by driver->>'family') as primary_families
from public.account_assessment a
join public.client c on c.workspace_id = a.workspace_id and c.id = a.client_id
where a.workspace_id = %(workspace_id)s and c.policy <> 'excluded'
  and a.run_kind = 'live' and a.as_of <= %(as_of)s
order by a.client_id, a.as_of
"""
_OUTCOMES = """
select o.client_id, o.event::text, o.occurred_at, o.notice_at
from public.outcome o
join public.client c on c.workspace_id = o.workspace_id and c.id = o.client_id
where o.workspace_id = %(workspace_id)s and c.policy <> 'excluded'
  and o.occurred_at <= %(as_of)s
order by o.client_id, o.occurred_at, o.id
"""
_IMPORTANT = """
select date_trunc('week', d.surfaced_on)::date as week, count(*)::int as count
from public.decision_card d
join public.client c on c.workspace_id = d.workspace_id and c.id = d.client_id
where d.workspace_id = %(workspace_id)s and c.policy <> 'excluded'
  and d.importance = 'important' and d.surfaced_on <= %(as_of)s
group by 1 order by 1
"""


class LearningRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def feedback(self, *, workspace_id: WorkspaceId, as_of: date) -> list[FeedbackRow]:
        with self._pool.connection() as conn:
            return [
                FeedbackRow(**r)
                for r in conn.execute(_FEEDBACK, {"workspace_id": workspace_id, "as_of": as_of})
            ]

    def assessments(self, *, workspace_id: WorkspaceId, as_of: date) -> list[AssessmentRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(_ASSESSMENTS, {"workspace_id": workspace_id, "as_of": as_of})
            return [
                AssessmentRow(
                    r["client_id"], r["as_of"], r["risk_state"], tuple(r["primary_families"])
                )
                for r in rows
            ]

    def outcomes(self, *, workspace_id: WorkspaceId, as_of: date) -> list[OutcomeRow]:
        with self._pool.connection() as conn:
            return [
                OutcomeRow(**r)
                for r in conn.execute(_OUTCOMES, {"workspace_id": workspace_id, "as_of": as_of})
            ]

    def important_per_week(
        self, *, workspace_id: WorkspaceId, as_of: date
    ) -> list[dict[str, object]]:
        with self._pool.connection() as conn:
            rows = conn.execute(_IMPORTANT, {"workspace_id": workspace_id, "as_of": as_of})
            return [{"week": r["week"], "count": r["count"]} for r in rows]
