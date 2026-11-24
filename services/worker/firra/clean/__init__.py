"""Deterministic cleaning of ingested text (P2 D4)."""

from firra.clean.email import CleanedEmail, clean_email, normalize_whitespace

__all__ = ["CleanedEmail", "clean_email", "normalize_whitespace"]
