"""Evidence verification: model output is trusted only where it matches the source (P2 D6)."""

from firra.verify.quotes import (
    MAX_QUOTE_CHARS,
    MAX_QUOTE_WORDS,
    MIN_QUOTE_WORDS,
    BodyIndex,
    Rejected,
    RejectReason,
    VerifiedQuote,
)

__all__ = [
    "MAX_QUOTE_CHARS",
    "MAX_QUOTE_WORDS",
    "MIN_QUOTE_WORDS",
    "BodyIndex",
    "RejectReason",
    "Rejected",
    "VerifiedQuote",
]
