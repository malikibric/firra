"""meeting.v1: the model may only reword the stored drafts, and failure keeps them."""

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from firra.brief.word import (
    MeetingQuestions,
    build_user_message,
    check_questions,
    word_questions,
)
from firra.db.jobs import ClaimedJob
from firra.db.meetings import StoredBrief
from firra.db.types import JobId, WorkspaceId
from firra.llm.prompts import load_prompt
from firra.tasks.context import TaskContext
from firra.tasks.handlers import brief as handler
from firra_contracts.generated.tasks.brief_meetings import BriefMeetingsPayload

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
DRAFTS = [
    "The client raised a problem (delivery is late); is it resolved on their side?",
    "Renewal is on 2026-11-01, with 29 days to the notice deadline; what needs agreeing?",
    "Who makes the client's decisions?",
]
QUESTIONS: list[dict[str, Any]] = [
    {"text": DRAFTS[0], "basis": {"kind": "complaint", "ref": str(UUID(int=3))}},
    {"text": DRAFTS[1], "basis": {"kind": "renewal", "ref": str(UUID(int=1))}},
    {"text": DRAFTS[2], "basis": {"kind": "unknown", "ref": "no_decision_maker"}},
]
GOOD = [
    "Has the late delivery the client raised been resolved on their side?",
    "Renewal is on 2026-11-01 with 29 days to the notice deadline; what do we still need to agree?",
    "Who makes the decisions at the client?",
]


def test_prompt_states_the_hard_rules() -> None:
    text = load_prompt("meeting.v1").text
    assert "exactly one reworded question per draft" in text
    assert "Never write numbers as words" in text
    assert "No quotation marks" in text


def test_user_message_wraps_drafts_as_untrusted() -> None:
    message = build_user_message("Acme", QUESTIONS)
    assert message.startswith("<brief_input>\nclient: Acme\ndrafts:\n[q1] kind: complaint")
    assert f'<source_record id="q1">\n{DRAFTS[0]}\n</source_record>' in message


def test_good_wording_passes() -> None:
    assert check_questions(GOOD, DRAFTS, allowed_text="Acme") == []


@pytest.mark.parametrize(
    ("texts", "violation"),
    [
        (GOOD[:2], "count:2!=3"),
        ([GOOD[0], " ", GOOD[2]], "empty:q2"),
        ([GOOD[0], GOOD[1], "x" * 241], "too_long:q3"),
        ([GOOD[0], GOOD[1].replace("29", "30"), GOOD[2]], "number:q2:30"),
        ([GOOD[0], GOOD[1].replace("2026-11-01", "2026-12-01"), GOOD[2]], "number:q2:2026-12-01"),
        ([GOOD[0], GOOD[1].replace("29 days", "twenty days"), GOOD[2]], "number_word:q2:twenty"),
        (['Was it "badly late"?', GOOD[1], GOOD[2]], "quotation:q1"),
        (["Was it late, ask a@b.example?", GOOD[1], GOOD[2]], "address:q1"),
    ],
)
def test_checks_reject(texts: list[str], violation: str) -> None:
    assert violation in check_questions(texts, DRAFTS)


@dataclass
class _Answer:
    value: MeetingQuestions


class _ScriptedLlm:
    def __init__(self, answers: list[list[str]]) -> None:
        self.answers = answers
        self.users: list[str] = []
        self.models = SimpleNamespace(model_for=lambda purpose: "test-model")

    def structured(self, **kwargs: Any) -> _Answer:
        assert kwargs["purpose"] == "synth" and kwargs["prompt_version"] == "meeting.v1"
        self.users.append(kwargs["user"])
        return _Answer(MeetingQuestions(questions=self.answers.pop(0)))


def _word(llm: _ScriptedLlm) -> Any:
    return word_questions(
        llm,  # type: ignore[arg-type]
        workspace_id=WorkspaceId(uuid4()),
        job_id=None,
        client_name="Acme",
        questions=QUESTIONS,
    )


def test_retry_is_told_the_broken_rules() -> None:
    bad = [GOOD[0], GOOD[1].replace("29", "30"), GOOD[2]]
    llm = _ScriptedLlm([bad, GOOD])
    worded = _word(llm)
    assert worded.texts == tuple(GOOD) and worded.attempts == 2
    assert "number:q2:30" in llm.users[1]


def test_failing_twice_keeps_the_drafts() -> None:
    bad = [GOOD[0], GOOD[1].replace("29", "30"), GOOD[2]]
    worded = _word(_ScriptedLlm([bad, bad]))
    assert worded.texts is None and worded.violations == ("number:q2:30",)


class _Repo:
    stored: StoredBrief | None = StoredBrief(UUID(int=7), "Acme", QUESTIONS)
    reworded: list[list[dict[str, Any]]]

    def __init__(self, pool: object) -> None:
        _Repo.reworded = []

    def upcoming(self, **kwargs: Any) -> list[object]:
        return [object()]

    def load_facts(self, **kwargs: Any) -> object:
        return object()

    def save(self, **kwargs: Any) -> bool:
        return True

    def stored_for_wording(self, **kwargs: Any) -> StoredBrief | None:
        return _Repo.stored

    def reword(self, **kwargs: Any) -> bool:
        assert kwargs["prompt_version"] == "meeting.v1" and kwargs["model"] == "test-model"
        _Repo.reworded.append(kwargs["questions"])
        return True


def _run(monkeypatch: pytest.MonkeyPatch, ctx: object) -> dict[str, Any]:
    monkeypatch.setattr(handler, "MeetingRepo", _Repo)
    job = ClaimedJob(WorkspaceId(UUID(int=1)), JobId(UUID(int=99)), "brief.meetings", 1, 3)
    return handler.handle_brief_meetings(
        cast(TaskContext, ctx),
        job,
        BriefMeetingsPayload.model_validate({"trigger": "manual"}),
        now=NOW,
    )


def test_handler_rewords_and_keeps_the_basis(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = SimpleNamespace(pool=None, processing_enabled=True, llm=_ScriptedLlm([GOOD]))
    result = _run(monkeypatch, ctx)
    assert result["briefs_written"] == 1 and result["briefs_reworded"] == 1
    assert [q["text"] for q in _Repo.reworded[0]] == GOOD
    assert [q["basis"] for q in _Repo.reworded[0]] == [q["basis"] for q in QUESTIONS]


def test_handler_is_free_when_processing_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _ScriptedLlm([GOOD])
    ctx = SimpleNamespace(pool=None, processing_enabled=False, llm=llm)
    assert _run(monkeypatch, ctx)["briefs_reworded"] == 0
    assert llm.users == [] and _Repo.reworded == []


def test_handler_skips_briefs_that_may_not_be_reworded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_Repo, "stored", None)
    llm = _ScriptedLlm([GOOD])
    ctx = SimpleNamespace(pool=None, processing_enabled=True, llm=llm)
    assert _run(monkeypatch, ctx)["briefs_reworded"] == 0
    assert llm.users == []


def test_a_model_failure_never_fails_the_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(_ScriptedLlm):
        def structured(self, **kwargs: Any) -> _Answer:
            raise RuntimeError("model down")

    ctx = SimpleNamespace(pool=None, processing_enabled=True, llm=Broken([]))
    result = _run(monkeypatch, ctx)
    assert result["briefs_written"] == 1 and result["briefs_reworded"] == 0
