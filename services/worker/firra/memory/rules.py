"""Pure memory rules; no model output or database access here."""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import yaml  # type: ignore[import-untyped]

from firra.signals.config import config_dir


@dataclass(frozen=True)
class MemoryConfig:
    version: str
    decision_maker_title_terms: tuple[str, ...]
    champion_window_days: int
    champion_min_messages: int
    result_days: int
    champion_contact_days: int


def load_config() -> MemoryConfig:
    path = config_dir() / "memory.yaml"
    data = yaml.safe_load(path.read_text())
    return MemoryConfig(
        version=data["version"],
        decision_maker_title_terms=tuple(data["decision_maker_title_terms"]),
        champion_window_days=data["champion_window_days"],
        champion_min_messages=data["champion_min_messages"],
        result_days=data["unknowns"]["result_days"],
        champion_contact_days=data["unknowns"]["champion_contact_days"],
    )


def normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def _mentions(quote: str, phrase: str) -> bool:
    value = normalized(phrase)
    return bool(value and re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", normalized(quote)))


def stakeholder_change_supported(
    *,
    person_name: str,
    role_title: str | None,
    quote: str,
    change: str,
    author_person_id: UUID | None,
    matched_person_id: UUID | None,
) -> bool:
    """Require the verified quote to support every person field used by memory."""
    named = _mentions(quote, person_name) or (
        change == "left" and matched_person_id is not None and author_person_id == matched_person_id
    )
    return named and (not role_title or _mentions(quote, role_title))


def dedupe_key(source_record_id: UUID | None, quote: str) -> str:
    value = (str(source_record_id) if source_record_id else "") + normalized(quote)
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def title_from_signature(signature: str | None, display_name: str | None) -> str | None:
    if not signature or not display_name:
        return None
    lines = [line.strip() for line in signature.splitlines() if line.strip()]
    for index, line in enumerate(lines[:-1]):
        if normalized(line) != normalized(display_name):
            continue
        candidate = lines[index + 1]
        if len(candidate) > 120 or re.search(r"\d|@|https?://|www\.", candidate, re.I):
            return None
        title = re.split(r"\s*[,|]\s*", candidate, maxsplit=1)[0].strip()
        if not title:
            return None
        return title
    return None


def is_decision_maker(title: str, cfg: MemoryConfig) -> bool:
    return any(
        re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", title, re.I)
        for term in cfg.decision_maker_title_terms
    )


def has_stated_budget(quote: str) -> bool:
    """Only a verified budget quote with an explicit amount removes the unknown."""
    return bool(
        re.search(r"\bbudget\b", quote, re.I)
        and re.search(r"(?:[$€£]\s*\d|\b\d[\d,.]*\s*(?:USD|EUR|GBP)\b)", quote, re.I)
    )


def commitment_status(status: str, due_date: date | None, as_of: date) -> str:
    if status in {"done", "cancelled"}:
        return status
    return "late" if due_date is not None and due_date < as_of else "open"


@dataclass(frozen=True)
class ChampionCandidate:
    id: UUID
    message_count: int
    first_seen_at: datetime | None


def choose_champion(candidates: list[ChampionCandidate], cfg: MemoryConfig) -> UUID | None:
    eligible = [c for c in candidates if c.message_count >= cfg.champion_min_messages]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda c: (
            -c.message_count,
            c.first_seen_at or datetime.max.replace(tzinfo=UTC),
            str(c.id),
        ),
    ).id


def card_changed(previous: dict[str, object] | None, current: dict[str, object]) -> bool:
    if previous is None:
        return True
    return {k: v for k, v in previous.items() if k != "as_of"} != {
        k: v for k, v in current.items() if k != "as_of"
    }
