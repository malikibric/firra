"""Select, word, check and persist this week's cards; model text never chooses the play."""

import time
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any
from uuid import UUID

from firra.cards.facts import render_facts
from firra.cards.schema import CardText, Fact
from firra.cards.select import select_candidates
from firra.cards.word import deterministic_card, word_card
from firra.db.cards import CardRepo
from firra.db.types import WorkspaceId
from firra.llm.service import Llm


def render_body(facts: Sequence[Fact], card: CardText) -> str:
    wording = f"{card.interpretation}\n\n{card.recommendation}"
    # Full facts stay in the structured column; the body is a bounded deterministic preview.
    text = "\n".join(f.text for f in facts)
    limit = 2000 - len(wording) - 2
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return f"{text}\n\n{wording}"


class CardsTimeBudgetError(Exception):
    """The task's time budget ran out between cards; the written ones are kept and the rest wait for
    the continuation (candidates already carded this week are skipped, so a rerun is idempotent)."""


def run_cards(
    repo: CardRepo,
    llm: Llm | None,
    *,
    workspace_id: WorkspaceId,
    job_id: UUID,
    week: date,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    candidates, existing = repo.candidates(workspace_id=workspace_id, week=week)
    selected = select_candidates(candidates, week, existing=existing)
    written = dropped = skipped = attempts = 0
    violations: Counter[str] = Counter()
    for candidate in selected:
        if deadline is not None and clock() >= deadline:
            raise CardsTimeBudgetError
        with repo.prepare(workspace_id=workspace_id, week=week, candidate=candidate) as prepared:
            if prepared is None:
                skipped += 1
                continue
            conn, quotes = prepared
            facts = render_facts(candidate.assessment, quotes)
            worded = (
                deterministic_card(play=candidate.assessment.play, facts=facts)
                if llm is None
                else word_card(
                    llm,
                    workspace_id=workspace_id,
                    job_id=job_id,
                    client_name=candidate.client_name,
                    play=candidate.assessment.play,
                    facts=facts,
                )
            )
            attempts += worded.attempts
            if worded.card is None:
                dropped += 1
                # Count classes only: violation details may include model-provided text.
                violations.update(v.split(":", 1)[0] for v in worded.violations)
                continue
            repo.save(
                conn,
                workspace_id=workspace_id,
                week=week,
                candidate=candidate,
                facts=facts,
                card=worded.card,
                body=render_body(facts, worded.card),
                priority=existing + written + 1,
                model=llm.models.model_for("synth") if llm is not None else "deterministic",
                prompt_version="cards.v1" if llm is not None else "facts.v1",
            )
            written += 1
    return {
        "week": week.isoformat(),
        "candidates": len(selected),
        "cards_written": written,
        "cards_dropped": dropped,
        "cards_skipped": skipped,
        "wording_attempts": attempts,
        "violations": dict(violations),
    }
