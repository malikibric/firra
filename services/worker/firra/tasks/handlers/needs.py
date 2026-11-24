"""needs.update: paid, workspace-scoped latent demand processing."""

from datetime import UTC, datetime, time
from typing import Any

from firra.ai.executor import is_local
from firra.db.jobs import ClaimedJob
from firra.db.needs import NeedsRepo
from firra.llm.service import BudgetExceededError
from firra.logging import get_logger
from firra.needs.config import load_config
from firra.needs.engine import run_needs
from firra.tasks.context import TaskContext
from firra.tasks.errors import PermanentTaskError
from firra_contracts.generated.tasks.needs_update import NeedsUpdatePayload


def handle_needs_update(
    ctx: TaskContext, job: ClaimedJob, payload: NeedsUpdatePayload
) -> dict[str, Any]:
    if is_local(ctx.pool):
        get_logger(__name__).info("needs.local_skipped", reason="topic_grouping_deferred_l3_10")
        return {
            "status": "skipped",
            "reason": "topic_grouping_deferred_l3_10",
            "trigger": payload.trigger.value,
        }
    if not ctx.processing_enabled:
        return {"status": "disabled", "trigger": payload.trigger.value}
    if ctx.llm is None:
        raise PermanentTaskError("llm_not_configured")
    try:
        config = load_config()
    except ValueError as exc:
        raise PermanentTaskError("needs_config_invalid") from exc
    ref = datetime.combine(payload.as_of, time.max, UTC) if payload.as_of else datetime.now(UTC)
    try:
        counts = run_needs(NeedsRepo(ctx.pool), ctx.llm, job=job, ref=ref, config=config)
    except BudgetExceededError as exc:
        raise PermanentTaskError("budget_exceeded") from exc
    return {**counts, "trigger": payload.trigger.value, "as_of": ref.date().isoformat()}
