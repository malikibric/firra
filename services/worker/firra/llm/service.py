"""Every model call goes through ``Llm``: budget check, structured output with one retry, usage.

Each attempt is recorded in ``llm_usage`` (tokens are billed even when the answer is unusable).
Logs carry model, purpose, tokens, cost and attempts only — never prompts or answers.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError

from firra.db.llm_usage import WorkspaceBudget
from firra.db.types import WorkspaceId
from firra.llm.models import ModelSet, Purpose
from firra.llm.prices import cost_usd
from firra.llm.untrusted import UNTRUSTED_NOTICE
from firra.llm.vertex import Embedding, Generation, LlmBlockedError, Usage
from firra.logging import get_logger

log = get_logger(__name__)

# A new workspace backfills 12 months in its first weeks (the design notes).
FIRST_MONTH = timedelta(days=31)
FIRST_MONTH_BUDGET_USD = Decimal(150)
# Budget crossings stored once per month for the app and notifications (P3/P7).
BUDGET_THRESHOLDS = (50, 80, 100)


class Backend(Protocol):
    def generate(
        self,
        model: str,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any],
        thinking_budget: int | None = None,
        max_output_tokens: int = 8192,
    ) -> Generation: ...

    def embed(self, model: str, text: str, *, task_type: str, dimensionality: int) -> Embedding: ...


class UsageStore(Protocol):
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
    ) -> None: ...

    def spent_since(self, *, workspace_id: WorkspaceId, since: datetime) -> Decimal: ...

    def budget(self, *, workspace_id: WorkspaceId) -> WorkspaceBudget | None: ...

    def note_budget_threshold(
        self, *, workspace_id: WorkspaceId, month: date, threshold: int
    ) -> bool: ...


class BudgetExceededError(Exception):
    """The workspace reached its monthly LLM budget; work waits until the budget allows it."""


class StructuredOutputError(Exception):
    """The answer did not match the schema after the retry."""


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema with ``$ref``s inlined (the API gets one self-contained schema)."""
    schema = model.model_json_schema()
    defs: dict[str, Any] = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return inline(defs[ref.removeprefix("#/$defs/")])
            return {key: inline(value) for key, value in node.items()}
        if isinstance(node, list):
            return [inline(item) for item in node]
        return node

    inlined: dict[str, Any] = inline(schema)
    return inlined


def monthly_limit(budget: WorkspaceBudget, now: datetime) -> Decimal:
    if now - budget.workspace_created_at < FIRST_MONTH:
        return max(budget.monthly_usd, FIRST_MONTH_BUDGET_USD)
    return budget.monthly_usd


@dataclass(frozen=True, slots=True)
class Structured[T: BaseModel]:
    value: T
    usage: Usage
    attempts: int


@dataclass
class Llm:
    backend: Backend
    models: ModelSet
    usage: UsageStore
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    hard_cap_usd: Decimal | None = None

    def check_budget(self, workspace_id: WorkspaceId) -> None:
        budget = self.usage.budget(workspace_id=workspace_id)
        if budget is None:
            raise BudgetExceededError("unknown workspace")
        now = self.clock()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = self.usage.spent_since(workspace_id=workspace_id, since=month_start)
        limit = monthly_limit(budget, now)
        if self.hard_cap_usd is not None:
            limit = min(limit, self.hard_cap_usd)
        for threshold in BUDGET_THRESHOLDS:
            if spent * 100 >= limit * threshold and self.usage.note_budget_threshold(
                workspace_id=workspace_id, month=month_start.date(), threshold=threshold
            ):
                log.warning("llm.budget_threshold", threshold=threshold)
        if spent >= limit:
            raise BudgetExceededError("monthly LLM budget reached")

    def _record(
        self,
        *,
        workspace_id: WorkspaceId,
        job_id: UUID | None,
        model: str,
        prompt_version: str,
        usage: Usage,
    ) -> Decimal:
        cost = cost_usd(
            model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            on=self.clock().date(),
        )
        self.usage.record(
            workspace_id=workspace_id,
            job_id=job_id,
            model=model,
            prompt_version=prompt_version,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
        )
        return cost

    def structured[T: BaseModel](
        self,
        *,
        workspace_id: WorkspaceId,
        job_id: UUID | None,
        purpose: Purpose,
        prompt_version: str,
        system: str,
        user: str,
        output: type[T],
        thinking_budget: int | None = None,
    ) -> Structured[T]:
        """Validated answer of ``output`` type; one retry on a schema mismatch (CLAUDE.md § 7)."""
        model = self.models.model_for(purpose)
        schema = json_schema_for(output)
        total = Usage()
        started = time.monotonic()
        for attempt in (1, 2):
            self.check_budget(workspace_id)
            try:
                generation = self.backend.generate(
                    model,
                    system=f"{system}\n\n{UNTRUSTED_NOTICE}",
                    user=user,
                    response_schema=schema,
                    thinking_budget=thinking_budget,
                )
            except LlmBlockedError as blocked:
                self._record(
                    workspace_id=workspace_id,
                    job_id=job_id,
                    model=model,
                    prompt_version=prompt_version,
                    usage=blocked.usage,
                )
                raise
            total += generation.usage
            cost = self._record(
                workspace_id=workspace_id,
                job_id=job_id,
                model=model,
                prompt_version=prompt_version,
                usage=generation.usage,
            )
            try:
                value = output.model_validate(json.loads(generation.text))
            except (ValueError, ValidationError):
                log.warning("llm.invalid_output", model=model, purpose=purpose, attempt=attempt)
                continue
            log.info(
                "llm.call",
                model=model,
                purpose=purpose,
                prompt_version=prompt_version,
                attempts=attempt,
                input_tokens=total.input_tokens,
                output_tokens=total.output_tokens,
                cost_usd=str(cost),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            return Structured(value=value, usage=total, attempts=attempt)
        raise StructuredOutputError(f"{purpose} output invalid after retry")

    def embed(
        self,
        *,
        workspace_id: WorkspaceId,
        job_id: UUID | None,
        text: str,
        task_type: str = "SEMANTIC_SIMILARITY",
        dimensionality: int = 768,
    ) -> list[float]:
        model = self.models.model_for("embed")
        self.check_budget(workspace_id)
        embedding = self.backend.embed(
            model, text, task_type=task_type, dimensionality=dimensionality
        )
        self._record(
            workspace_id=workspace_id,
            job_id=job_id,
            model=model,
            prompt_version="embed",
            usage=embedding.usage,
        )
        return embedding.values
