"""The cards task stops between cards when its time budget is gone and continues later."""

import time
from datetime import date
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from firra.cards import persist
from firra.cards.persist import CardsTimeBudgetError, run_cards
from firra.db.types import WorkspaceId
from firra.tasks.errors import ContinueTask
from firra.tasks.handlers import cards as handler
from firra_contracts.generated.tasks.synth_cards import SynthCardsPayload

PAYLOAD = SynthCardsPayload.model_validate({"trigger": "manual", "week": "2026-09-28"})


def test_run_cards_stops_before_the_next_card_once_the_deadline_has_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Repo:
        def candidates(self, **_: Any) -> tuple[list[Any], int]:
            return [], 0

        def prepare(self, **_: Any) -> Any:
            raise AssertionError("no card may start after the deadline")

    monkeypatch.setattr(persist, "select_candidates", lambda *a, **k: ["a", "b"])
    with pytest.raises(CardsTimeBudgetError):
        run_cards(
            Repo(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            workspace_id=WorkspaceId(uuid4()),
            job_id=uuid4(),
            week=date(2026, 9, 28),
            deadline=10.0,
            clock=lambda: 11.0,
        )


def test_the_handler_turns_an_exhausted_budget_into_a_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def over_budget(*_: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        raise CardsTimeBudgetError

    monkeypatch.setattr(handler, "run_cards", over_budget)
    ctx: Any = SimpleNamespace(
        processing_enabled=True, llm=object(), pool=object(), time_budget_seconds=60.0
    )
    job: Any = SimpleNamespace(workspace_id=uuid4(), id=uuid4())
    before = time.monotonic()
    with pytest.raises(ContinueTask) as raised:
        handler.handle_synth_cards(ctx, job, PAYLOAD)
    assert raised.value.progress == {"trigger": "manual", "week": "2026-09-28"}
    assert before + 59 <= seen["deadline"] <= time.monotonic() + 61
