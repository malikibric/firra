"""llm_usage rows (tokens and cost of every model call) and the workspace LLM budget."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId

_RECORD = """
insert into public.llm_usage
  (workspace_id, job_id, model, prompt_version, input_tokens, output_tokens, cost_usd)
values
  (%(workspace_id)s, %(job_id)s, %(model)s, %(prompt_version)s, %(input_tokens)s,
   %(output_tokens)s, %(cost_usd)s)
"""
_SPENT = """
select coalesce(sum(cost_usd), 0) as spent
from public.llm_usage
where workspace_id = %(workspace_id)s and created_at >= %(since)s
"""
_BUDGET = """
select llm_budget_usd_month, created_at from public.workspace where id = %(workspace_id)s
"""
_THRESHOLD = """
insert into public.llm_budget_event (workspace_id, month, threshold)
values (%(workspace_id)s, %(month)s, %(threshold)s)
on conflict (workspace_id, month, threshold) do nothing
returning id
"""


@dataclass(frozen=True, slots=True)
class WorkspaceBudget:
    monthly_usd: Decimal
    workspace_created_at: datetime


class LlmUsageRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def record(
        self,
        *,
        workspace_id: WorkspaceId,
        job_id: UUID | None,
        model: str,
        prompt_version: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: Decimal,
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                _RECORD,
                {
                    "workspace_id": workspace_id,
                    "job_id": job_id,
                    "model": model,
                    "prompt_version": prompt_version,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost_usd,
                },
            )

    def spent_since(self, *, workspace_id: WorkspaceId, since: datetime) -> Decimal:
        with self._pool.connection() as conn:
            row = conn.execute(_SPENT, {"workspace_id": workspace_id, "since": since}).fetchone()
        return Decimal(row["spent"]) if row else Decimal(0)

    def budget(self, *, workspace_id: WorkspaceId) -> WorkspaceBudget | None:
        with self._pool.connection() as conn:
            row = conn.execute(_BUDGET, {"workspace_id": workspace_id}).fetchone()
        if row is None:
            return None
        return WorkspaceBudget(Decimal(row["llm_budget_usd_month"]), row["created_at"])

    def note_budget_threshold(
        self, *, workspace_id: WorkspaceId, month: date, threshold: int
    ) -> bool:
        """Stores a budget crossing once per month; True when it is new."""
        with self._pool.connection() as conn:
            row = conn.execute(
                _THRESHOLD,
                {"workspace_id": workspace_id, "month": month, "threshold": threshold},
            ).fetchone()
        return row is not None
