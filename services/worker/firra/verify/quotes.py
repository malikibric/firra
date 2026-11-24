"""Verbatim quote verification (P2 D6).

A quote from the model is valid only if it occurs as one contiguous, case-sensitive substring of
the record's cleaned body after both texts are normalized the same way: Unicode NFKC (applied per
base character with its combining marks, so decomposed accents match composed ones), curly and
angled quote marks folded to ``'`` and ``"``, invisible format characters (zero-width spaces,
soft hyphens) ignored, and whitespace runs collapsed to one space.

Invalid quotes are rejected with a reason and counted by the caller, never repaired. A valid
quote is stored as the matching span of the cleaned body itself (whitespace collapsed, format
characters removed), so what is stored is always the source text, not the model's rendering of
it. Offsets are Python code point offsets into the cleaned body.
"""

import unicodedata
from dataclasses import dataclass
from typing import Literal

MIN_QUOTE_WORDS = 3
MAX_QUOTE_WORDS = 25
MAX_QUOTE_CHARS = 300  # evidence.quote varchar(300)

RejectReason = Literal["empty", "too_short", "too_long", "not_found"]

_QUOTE_MARKS = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
        "\u2039": "'", "\u203a": "'",
        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
        "\u00ab": '"', "\u00bb": '"',
    }
)  # fmt: skip


@dataclass(frozen=True)
class VerifiedQuote:
    quote: str
    start: int
    end: int

    @property
    def locator(self) -> dict[str, int]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True)
class Rejected:
    reason: RejectReason


def _normalize(text: str) -> tuple[str, list[int], list[int]]:
    """Normalized text plus, per output character, the source span [start, end) it came from."""
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(text[j]):
            j += 1
        for ch in unicodedata.normalize("NFKC", text[i:j]).translate(_QUOTE_MARKS):
            if unicodedata.category(ch) == "Cf":
                continue
            if ch.isspace():
                if out and out[-1] == " ":
                    ends[-1] = j
                    continue
                ch = " "
            out.append(ch)
            starts.append(i)
            ends.append(j)
        i = j
    return "".join(out), starts, ends


def _source_text(span: str) -> str:
    visible = "".join(c for c in span if unicodedata.category(c) != "Cf")
    return " ".join(visible.split())


def _word_count(text: str) -> int:
    return len(text.split())


class BodyIndex:
    """Normalized view of one cleaned body, reused for every quote extracted from it."""

    def __init__(self, body: str) -> None:
        self.body = body
        self._text, self._starts, self._ends = _normalize(body)

    def verify(self, quote: str) -> VerifiedQuote | Rejected:
        needle = _normalize(quote)[0].strip()
        if not needle:
            return Rejected("empty")
        words = _word_count(needle)
        if words < MIN_QUOTE_WORDS:
            return Rejected("too_short")
        if words > MAX_QUOTE_WORDS:
            return Rejected("too_long")

        pos = self._text.find(needle)
        if pos < 0:
            return Rejected("not_found")
        start = self._starts[pos]
        end = self._ends[pos + len(needle) - 1]
        stored = _source_text(self.body[start:end])
        if len(stored) > MAX_QUOTE_CHARS or _word_count(stored) > MAX_QUOTE_WORDS:
            return Rejected("too_long")
        if _word_count(stored) < MIN_QUOTE_WORDS:
            return Rejected("too_short")
        return VerifiedQuote(quote=stored, start=start, end=end)
