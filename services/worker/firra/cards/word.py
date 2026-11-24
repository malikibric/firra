"""Model wording of one card (prompt cards.v1) with deterministic checks and one retry."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from firra.cards.checks import check_card
from firra.cards.schema import CardText, Fact
from firra.db.types import WorkspaceId
from firra.llm.prompts import load_prompt
from firra.llm.service import Llm
from firra.llm.untrusted import wrap_untrusted

PROMPT_VERSION = "cards.v1"

PLAY_MEANING: dict[str, str] = {
    "fix_first": "fix the delivery or relationship problem before anything commercial",
    "stakeholder_remap": "re-establish who the decision makers and contacts are",
    "renewal_prep": "prepare the renewal conversation before the notice deadline",
    "founder_review_call": "the founder calls the client for a review",
    "expansion_conversation": "open a conversation about more work",
}


@dataclass(frozen=True, slots=True)
class Worded:
    card: CardText | None
    attempts: int
    violations: tuple[str, ...]


def build_user_message(client_name: str, play: str, facts: Sequence[Fact]) -> str:
    lines = [
        "<card_input>",
        f"client: {client_name}",
        f"play: {play} ({PLAY_MEANING.get(play, play)})",
        "facts:",
    ]
    for fact in facts:
        lines.append(f"[{fact.id}] {fact.text}")
        for n, quote in enumerate(fact.quotes, start=1):
            lines.append(wrap_untrusted(f"{fact.id}-q{n}", quote))
    lines.append("</card_input>")
    return "\n".join(lines)


def word_card(
    llm: Llm,
    *,
    workspace_id: WorkspaceId,
    job_id: UUID | None,
    client_name: str,
    play: str,
    facts: Sequence[Fact],
) -> Worded:
    """Two attempts; the second one is told which rules the first answer broke. A card that still
    fails is dropped (``card`` is None) and its violations are returned for counting."""
    prompt = load_prompt(PROMPT_VERSION)
    user = build_user_message(client_name, play, facts)
    violations: list[str] = []
    for attempt in (1, 2):
        message = user
        if violations:
            message += (
                "\n\nYour previous answer broke these rules: "
                + ", ".join(violations)
                + ". Answer again and follow every rule."
            )
        answer = llm.structured(
            workspace_id=workspace_id,
            job_id=job_id,
            purpose="synth",
            prompt_version=prompt.version,
            system=prompt.text,
            user=message,
            output=CardText,
        )
        violations = check_card(answer.value, facts, allowed_text=client_name)
        if not violations:
            return Worded(card=answer.value, attempts=attempt, violations=())
    return Worded(card=None, attempts=2, violations=tuple(violations))


_TITLES = {
    "fix_first": "Resolve the client problem",
    "stakeholder_remap": "Review the client's stakeholders",
    "renewal_prep": "Prepare the renewal conversation",
    "founder_review_call": "Arrange a founder review call",
    "expansion_conversation": "Discuss the client's stated need",
}


def deterministic_card(*, play: str, facts: Sequence[Fact]) -> Worded:
    """Render stored facts and the selected play; retain the same number/date/citation checks."""
    text = facts[0].text if facts else ""
    interpretation = text if len(text) <= 600 else text[:600].rsplit(" ", 1)[0]
    card = CardText(
        title=_TITLES.get(play, "Review the client relationship"),
        interpretation=interpretation,
        recommendation=PLAY_MEANING.get(
            play, "review the stored facts with the client"
        ).capitalize()
        + ".",
        fact_ids=[f.id for f in facts[:20]],
    )
    violations = check_card(card, facts)
    return Worded(card=None if violations else card, attempts=0, violations=tuple(violations))
