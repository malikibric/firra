"""Client-scoped source search: matching, excerpts and caps (source layer S2, pure functions).

Input is text the engine already decrypted and cleaned for ONE client; output is bounded and made
safe to hand to the owner's AI: control and zero-width characters stripped, whitespace collapsed,
excerpts cut around the first match. Nothing here touches a database or a provider.
"""

import unicodedata
from dataclasses import dataclass
from datetime import datetime

EXCERPT_CHARS = 280
SUBJECT_CHARS = 200
CONTEXT_CHARS = 1500
ANSWER_CHARS = 8000
MAX_QUERY_TERMS = 8


def clean_text(text: str) -> str:
    """Plain text safe to show: no control or format characters, single spaces."""
    out: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if category in ("Cc", "Zl", "Zp") or ch in "\t\n\r":
            out.append(" ")
        elif category in ("Cf", "Cs", "Co", "Cn"):
            continue
        else:
            out.append(ch)
    return " ".join("".join(out).split())


def _fold(text: str) -> tuple[str, list[int]]:
    """Accent- and case-insensitive form plus, per folded character, its index in ``text``."""
    folded: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        for piece in unicodedata.normalize("NFKD", ch):
            if unicodedata.combining(piece):
                continue
            for lowered in piece.casefold():
                folded.append(lowered)
                index.append(i)
    return "".join(folded), index


def query_terms(query: str) -> list[str]:
    terms = [t for t in _fold(clean_text(query))[0].split(" ") if t]
    return list(dict.fromkeys(terms))[:MAX_QUERY_TERMS]


@dataclass(frozen=True, slots=True)
class Match:
    score: int
    excerpt: str


def match(text: str, terms: list[str], *, extra: str = "") -> Match | None:
    """All terms must occur in ``extra`` (subject) plus ``text``; the excerpt is cut from ``text``.

    A hit that matches only in ``extra`` gets the start of ``text`` as its excerpt.
    """
    if not terms:
        return None
    body, body_index = _fold(text)
    haystack = _fold(extra)[0] + " " + body
    if any(term not in haystack for term in terms):
        return None
    score = sum(haystack.count(term) for term in terms)
    starts = [pos for term in terms if (pos := body.find(term)) >= 0]
    if starts:
        first = min(starts)
        centre = body_index[first] if first < len(body_index) else 0
        begin = max(0, centre - EXCERPT_CHARS // 3)
    else:
        begin = 0
    excerpt = clean_text(text[begin : begin + EXCERPT_CHARS + 40])[:EXCERPT_CHARS]
    return Match(score=score, excerpt=excerpt)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One stored record of the client, already decrypted by the repository."""

    key: str
    occurred_at: datetime
    text: str
    subject: str | None


def rank(
    candidates: list[Candidate], terms: list[str], limit: int
) -> list[tuple[Candidate, Match]]:
    hits = []
    for candidate in candidates:
        found = match(candidate.text, terms, extra=candidate.subject or "")
        if found is not None:
            hits.append((candidate, found))
    hits.sort(key=lambda h: (-h[1].score, -h[0].occurred_at.timestamp(), h[0].key))
    return hits[:limit]


def fit_budget(chunks: list[int], budget: int = ANSWER_CHARS) -> int:
    """How many leading chunks (character counts) fit the answer budget."""
    used = 0
    for i, size in enumerate(chunks):
        if used + size > budget:
            return i
        used += size
    return len(chunks)
