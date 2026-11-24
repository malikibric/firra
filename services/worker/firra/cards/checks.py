"""Deterministic checks on a worded card (P2b D9): the model may only word the facts.

A card is rejected when it cites an unknown fact, leaves a field empty, contains a number or date
that no fact contains, spells a number as a word, or puts quotation marks around more than three
words (quotes are shown from evidence, never written by the model).
"""

import re
from collections.abc import Sequence

from firra.cards.schema import CardText, Fact

# An ISO date is one token, so a card cannot build a new date from numbers the facts contain.
_NUMBER = re.compile(r"\d{4}-\d{2}-\d{2}|\d+(?:[.,:]\d+)*")
_NUMBER_WORDS = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety|hundred|thousand|dozen|dozens)\b",
    re.IGNORECASE,
)
_QUOTED = re.compile(
    '["\u201c\u201d\u201e\u00ab\u00bb]([^"\u201c\u201d\u201e\u00ab\u00bb]+)["\u201c\u201d\u201e\u00ab\u00bb]'
    "|\u2018([^\u2019]+)\u2019"
)


def _numbers(text: str) -> set[str]:
    return {n.rstrip(".,:") for n in _NUMBER.findall(text)}


def check_card(card: CardText, facts: Sequence[Fact], *, allowed_text: str = "") -> list[str]:
    """Violations (empty when the card may be stored). ``allowed_text`` adds context the prompt
    showed besides the facts, e.g. the client's name."""
    problems: list[str] = []
    fields = {
        "title": card.title,
        "interpretation": card.interpretation,
        "recommendation": card.recommendation,
    }
    for name, value in fields.items():
        if not value.strip():
            problems.append(f"empty:{name}")
    known = {f.id for f in facts}
    if not card.fact_ids:
        problems.append("no_fact_ids")
    problems.extend(f"unknown_fact:{fid}" for fid in card.fact_ids if fid not in known)
    source = " ".join([*(f.text for f in facts), *(q for f in facts for q in f.quotes)])
    allowed = _numbers(source + " " + allowed_text)
    for name, value in fields.items():
        problems.extend(f"number:{name}:{n}" for n in sorted(_numbers(value) - allowed))
        problems.extend(
            f"number_word:{name}:{w.lower()}" for w in sorted(set(_NUMBER_WORDS.findall(value)))
        )
        for match in _QUOTED.finditer(value):
            quoted = match.group(1) or match.group(2) or ""
            if len(quoted.split()) > 3:
                problems.append(f"quotation:{name}")
    return problems
