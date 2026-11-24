"""Model wording of a meeting brief's questions (prompt meeting.v1) with deterministic checks.

The deterministic brief is stored first; this only rewords the text of its questions. A question's
basis (kind and ref) never changes, and an answer that breaks a check leaves the draft in place.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from firra.db.types import WorkspaceId
from firra.llm.prompts import load_prompt
from firra.llm.service import Llm
from firra.llm.untrusted import wrap_untrusted

PROMPT_VERSION = "meeting.v1"
MAX_QUESTION_CHARS = 240

# An ISO date is one token, so wording cannot build a new date from numbers the draft contains.
_NUMBER = re.compile(r"\d{4}-\d{2}-\d{2}|\d+(?:[.,:]\d+)*")
_NUMBER_WORDS = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety|hundred|thousand|dozen|dozens)\b",
    re.IGNORECASE,
)
# Straight and curly quotes, guillemets and the backtick: the model writes no quotations.
_QUOTE_CODEPOINTS = (0x22, 0x201C, 0x201D, 0x201E, 0xAB, 0xBB, 0x2018, 0x2019, 0x60)
_QUOTE_MARKS = re.compile("[" + "".join(re.escape(chr(c)) for c in _QUOTE_CODEPOINTS) + "]")
_ADDRESS = re.compile(r"[^\s@]+@[^\s@]+")


class MeetingQuestions(BaseModel):
    """Structured output of meeting.v1."""

    model_config = ConfigDict(extra="ignore")

    questions: list[str] = Field(max_length=3)


@dataclass(frozen=True, slots=True)
class Worded:
    texts: tuple[str, ...] | None
    attempts: int
    violations: tuple[str, ...]


def _numbers(text: str) -> set[str]:
    return {n.rstrip(".,:") for n in _NUMBER.findall(text)}


def check_questions(
    texts: Sequence[str], drafts: Sequence[str], *, allowed_text: str = ""
) -> list[str]:
    """Violations (empty when the wording may replace the drafts)."""
    if len(texts) != len(drafts):
        return [f"count:{len(texts)}!={len(drafts)}"]
    allowed = _numbers(" ".join(drafts) + " " + allowed_text)
    problems: list[str] = []
    for n, text in enumerate(texts, start=1):
        if not text.strip():
            problems.append(f"empty:q{n}")
        if len(text) > MAX_QUESTION_CHARS:
            problems.append(f"too_long:q{n}")
        problems.extend(f"number:q{n}:{x}" for x in sorted(_numbers(text) - allowed))
        problems.extend(
            f"number_word:q{n}:{w.lower()}" for w in sorted(set(_NUMBER_WORDS.findall(text)))
        )
        if _QUOTE_MARKS.search(text):
            problems.append(f"quotation:q{n}")
        if _ADDRESS.search(text):
            problems.append(f"address:q{n}")
    return problems


def build_user_message(client_name: str, questions: Sequence[dict[str, Any]]) -> str:
    lines = ["<brief_input>", f"client: {client_name}", "drafts:"]
    for n, question in enumerate(questions, start=1):
        lines.append(f"[q{n}] kind: {question['basis']['kind']}")
        lines.append(wrap_untrusted(f"q{n}", question["text"]))
    lines.append("</brief_input>")
    return "\n".join(lines)


def word_questions(
    llm: Llm,
    *,
    workspace_id: WorkspaceId,
    job_id: UUID | None,
    client_name: str,
    questions: Sequence[dict[str, Any]],
) -> Worded:
    """Two attempts; the second one is told which rules the first answer broke. When it still
    fails, ``texts`` is None and the drafts stay."""
    prompt = load_prompt(PROMPT_VERSION)
    user = build_user_message(client_name, questions)
    drafts = [q["text"] for q in questions]
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
            output=MeetingQuestions,
        )
        texts = [t.strip() for t in answer.value.questions]
        violations = check_questions(texts, drafts, allowed_text=client_name)
        if not violations:
            return Worded(texts=tuple(texts), attempts=attempt, violations=())
    return Worded(texts=None, attempts=2, violations=tuple(violations))
