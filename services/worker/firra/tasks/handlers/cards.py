"""synth.cards: weekly decision cards, with paid wording behind the processing switch."""

import time
from typing import Any

from firra.ai.executor import is_local
from firra.cards.persist import CardsTimeBudgetError, run_cards
from firra.db.cards import CardRepo
from firra.db.jobs import ClaimedJob
from firra.db.types import WorkspaceId
from firra.llm.service import BudgetExceededError
from firra.tasks.context import TaskContext
from firra.tasks.errors import ContinueTask, PermanentTaskError
from firra_contracts.generated.tasks.synth_cards import SynthCardsPayload


def handle_synth_cards(
    ctx: TaskContext, job: ClaimedJob, payload: SynthCardsPayload
) -> dict[str, Any]:
    local = is_local(ctx.pool)
    if not local and not ctx.processing_enabled:
        return {"status": "disabled", "trigger": payload.trigger.value}
    if not local and ctx.llm is None:
        raise PermanentTaskError("llm_not_configured")
    if payload.week.weekday() != 0:
        raise PermanentTaskError("cards_week_not_monday")
    try:
        return {
            "trigger": payload.trigger.value,
            **run_cards(
                CardRepo(ctx.pool),
                None if local else ctx.llm,
                workspace_id=WorkspaceId(job.workspace_id),
                job_id=job.id,
                week=payload.week,
                deadline=time.monotonic() + ctx.time_budget_seconds,
            ),
        }
    except CardsTimeBudgetError as exc:
        raise ContinueTask(
            {"trigger": payload.trigger.value, "week": payload.week.isoformat()}
        ) from exc
    except BudgetExceededError as exc:
        raise PermanentTaskError("budget_exceeded") from exc
