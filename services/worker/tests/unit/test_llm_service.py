from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from firra.db.llm_usage import WorkspaceBudget
from firra.db.types import WorkspaceId
from firra.llm.models import LlmNotConfiguredError, ModelSet
from firra.llm.service import BudgetExceededError, Llm, StructuredOutputError, json_schema_for
from firra.llm.untrusted import UNTRUSTED_NOTICE
from firra.llm.vertex import Embedding, Generation, LlmBlockedError, Usage

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
WS = WorkspaceId(uuid4())
MODELS = ModelSet(
    location="eu",
    extract="gemini-3.5-flash-lite",
    synth="gemini-3.8-flash",
    embed="gemini-embedding-001",
    synthdata=None,
)


class Item(BaseModel):
    quote: str


class Answer(BaseModel):
    items: list[Item]


@dataclass
class FakeBackend:
    answers: list[str | LlmBlockedError]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def generate(self, model: str, **kwargs: Any) -> Generation:
        self.calls.append({"model": model, **kwargs})
        answer = self.answers.pop(0)
        if isinstance(answer, LlmBlockedError):
            raise answer
        return Generation(text=answer, usage=Usage(input_tokens=1000, output_tokens=100))

    def embed(self, model: str, text: str, *, task_type: str, dimensionality: int) -> Embedding:
        return Embedding(values=[0.0] * dimensionality, usage=Usage(input_tokens=2))


@dataclass
class FakeUsage:
    spent: Decimal = Decimal(0)
    monthly: Decimal = Decimal(50)
    created_at: datetime = NOW - timedelta(days=90)
    rows: list[dict[str, Any]] = field(default_factory=list)
    thresholds: set[tuple[date, int]] = field(default_factory=set)

    def record(self, **row: Any) -> None:
        self.rows.append(row)

    def note_budget_threshold(
        self, *, workspace_id: WorkspaceId, month: date, threshold: int
    ) -> bool:
        new = (month, threshold) not in self.thresholds
        self.thresholds.add((month, threshold))
        return new

    def spent_since(self, *, workspace_id: WorkspaceId, since: datetime) -> Decimal:
        assert since == datetime(2026, 10, 1, tzinfo=UTC)
        return self.spent

    def budget(self, *, workspace_id: WorkspaceId) -> WorkspaceBudget | None:
        return WorkspaceBudget(self.monthly, self.created_at)


def _llm(backend: FakeBackend, usage: FakeUsage | None = None) -> Llm:
    return Llm(backend=backend, models=MODELS, usage=usage or FakeUsage(), clock=lambda: NOW)


def _extract(llm: Llm, job_id: UUID | None = None) -> Any:
    return llm.structured(
        workspace_id=WS,
        job_id=job_id,
        purpose="extract",
        prompt_version="extract.v1",
        system="Extract items.",
        user="<source_record>…</source_record>",
        output=Answer,
    )


def test_valid_answer_is_parsed_and_its_usage_recorded() -> None:
    backend = FakeBackend(['{"items": [{"quote": "we are unhappy"}]}'])
    usage = FakeUsage()
    job_id = uuid4()
    result = _extract(_llm(backend, usage), job_id)

    assert result.value == Answer(items=[Item(quote="we are unhappy")])
    assert result.attempts == 1
    call = backend.calls[0]
    assert call["model"] == "gemini-3.5-flash-lite"
    assert call["system"].endswith(UNTRUSTED_NOTICE)
    assert call["response_schema"]["properties"]["items"]["items"]["properties"]["quote"]
    assert usage.rows == [
        {
            "workspace_id": WS,
            "job_id": job_id,
            "model": "gemini-3.5-flash-lite",
            "prompt_version": "extract.v1",
            "input_tokens": 1000,
            "output_tokens": 100,
            "cost_usd": Decimal("0.000605"),
        }
    ]


def test_schema_mismatch_is_retried_once_and_both_attempts_are_billed() -> None:
    backend = FakeBackend(['{"items": "nope"}', '{"items": []}'])
    usage = FakeUsage()
    result = _extract(_llm(backend, usage))
    assert (result.attempts, result.usage) == (2, Usage(2000, 200))
    assert len(usage.rows) == 2


def test_second_mismatch_fails() -> None:
    usage = FakeUsage()
    with pytest.raises(StructuredOutputError):
        _extract(_llm(FakeBackend(["not json", '{"x": 1}']), usage))
    assert len(usage.rows) == 2


def test_blocked_answers_are_billed_and_raised() -> None:
    usage = FakeUsage()
    blocked = LlmBlockedError("SAFETY", Usage(input_tokens=300))
    with pytest.raises(LlmBlockedError):
        _extract(_llm(FakeBackend([blocked]), usage))
    assert usage.rows[0]["input_tokens"] == 300


def test_budget_stops_calls_before_they_are_made() -> None:
    backend = FakeBackend(['{"items": []}'])
    with pytest.raises(BudgetExceededError):
        _extract(_llm(backend, FakeUsage(spent=Decimal(50))))
    assert backend.calls == []


def test_first_month_allows_the_backfill_budget() -> None:
    new_workspace = FakeUsage(spent=Decimal(120), created_at=NOW - timedelta(days=10))
    assert _extract(_llm(FakeBackend(['{"items": []}']), new_workspace)).attempts == 1
    with pytest.raises(BudgetExceededError):
        _extract(_llm(FakeBackend([]), FakeUsage(spent=Decimal(150), created_at=NOW)))


def test_missing_model_is_a_configuration_error() -> None:
    with pytest.raises(LlmNotConfiguredError):
        _llm(FakeBackend([])).structured(
            workspace_id=WS,
            job_id=None,
            purpose="synthdata",
            prompt_version="x",
            system="s",
            user="u",
            output=Answer,
        )


def test_embeddings_are_billed() -> None:
    usage = FakeUsage()
    values = _llm(FakeBackend([]), usage).embed(workspace_id=WS, job_id=None, text="blue sky")
    assert len(values) == 768
    assert usage.rows[0]["model"] == "gemini-embedding-001"


def test_json_schema_has_no_refs() -> None:
    schema = json_schema_for(Answer)
    assert "$defs" not in schema
    assert "$ref" not in str(schema)


def test_budget_thresholds_are_noted_once_per_month() -> None:
    usage = FakeUsage(spent=Decimal(41))
    llm = _llm(FakeBackend(['{"items": []}', '{"items": []}']), usage)
    _extract(llm)
    _extract(llm)
    assert usage.thresholds == {(date(2026, 10, 1), 50), (date(2026, 10, 1), 80)}


def test_reaching_the_budget_notes_100_percent() -> None:
    usage = FakeUsage(spent=Decimal(50))
    with pytest.raises(BudgetExceededError):
        _extract(_llm(FakeBackend([]), usage))
    assert (date(2026, 10, 1), 100) in usage.thresholds


def test_hard_cap_lowers_first_month_budget() -> None:
    backend = FakeBackend([])
    usage = FakeUsage(spent=Decimal(5), created_at=NOW)
    llm = _llm(backend, usage)
    llm.hard_cap_usd = Decimal(5)
    with pytest.raises(BudgetExceededError):
        _extract(llm)
    assert backend.calls == []


def test_hard_cap_never_raises_existing_limit() -> None:
    usage = FakeUsage(spent=Decimal(3), monthly=Decimal(3))
    llm = _llm(FakeBackend([]), usage)
    llm.hard_cap_usd = Decimal(5)
    with pytest.raises(BudgetExceededError):
        llm.check_budget(WS)


@pytest.mark.parametrize(
    ("spent", "thresholds"),
    [
        ("2.49", set()),
        ("2.5", {50}),
        ("4", {50, 80}),
        ("5", {50, 80, 100}),
    ],
)
def test_thresholds_use_capped_limit(spent: str, thresholds: set[int]) -> None:
    usage = FakeUsage(spent=Decimal(spent), created_at=NOW)
    llm = _llm(FakeBackend([]), usage)
    llm.hard_cap_usd = Decimal(5)
    if Decimal(spent) >= 5:
        with pytest.raises(BudgetExceededError):
            llm.check_budget(WS)
    else:
        llm.check_budget(WS)
    assert usage.thresholds == {(date(2026, 10, 1), t) for t in thresholds}
