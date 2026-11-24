"""Deterministic briefs: policy, evidence, item ordering and bounded questions."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from firra.brief.meeting import BriefItem, MeetingConfig, MeetingFacts, build_content, load_config
from firra.db.jobs import ClaimedJob
from firra.db.types import JobId, WorkspaceId
from firra.tasks.context import TaskContext
from firra.tasks.handlers import brief as handler
from firra.tasks.queue import QUEUE_FOR_TASK
from firra.tasks.registry import REGISTRY
from firra_contracts.generated.tasks.brief_meetings import BriefMeetingsPayload

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
CLIENT = UUID(int=1)
EVIDENCE = UUID(int=2)
CFG = MeetingConfig()
EMPTY = MeetingFacts(CLIENT, NOW, 14, NOW - timedelta(days=10))


def item(n: int, text: str = "send the plan", **fields: Any) -> BriefItem:
    return BriefItem(UUID(int=n), text, (EVIDENCE,), **fields)


def kinds(content: dict[str, Any]) -> list[str]:
    return [q["basis"]["kind"] for q in content["questions"]]


def test_full_statements_links_dates_and_priority() -> None:
    facts = replace(
        EMPTY,
        complaints=(item(3, "delivery is late", subtype="delay"),),
        commitments=(
            item(4, due=date(2026, 10, 5)),
            item(5, due=date(2026, 10, 1), late=True, side="client"),
        ),
        stakeholders=(item(6, "a decision maker joined", occurred_at=NOW),),
        renewal_date=date(2026, 11, 1),
        notice_period_days=30,
        unknowns=({"code": "no_stated_goal", "detail": "missing"},),
    )
    built = build_content(facts, policy="full", cfg=CFG)
    assert built.basis == "evidence" and built.evidence_ids == (EVIDENCE,)
    assert kinds(built.content) == ["complaint", "commitment", "commitment"]
    assert [q["basis"]["ref"] for q in built.content["questions"]] == [
        str(UUID(int=n)) for n in (3, 5, 4)
    ]
    assert built.content["open_commitments"][0]["late"] is True
    assert built.content["renewal"] == {"date": "2026-11-01", "days_to_notice": -1}
    assert built.content["since_last_meeting"] == {
        "records": 14,
        "last_held_at": EMPTY.last_held_at.isoformat() if EMPTY.last_held_at else None,
    }


def test_metadata_drops_all_body_derived_rows_and_evidence() -> None:
    secret = "PRIVATE BODY STATEMENT"
    facts = replace(
        EMPTY,
        commitments=(item(3, secret),),
        complaints=(item(4, secret),),
        stakeholders=(item(5, secret, occurred_at=NOW),),
        renewal_date=date(2026, 11, 1),
        unknowns=({"code": "budget_unknown", "detail": "Budget unknown."},),
    )
    built = build_content(facts, policy="metadata_only", cfg=CFG)
    assert built.basis == "metadata" and built.evidence_ids == ()
    assert secret not in str(built.content) and str(EVIDENCE) not in str(built.content)
    assert set(built.content) == {"since_last_meeting", "renewal", "unknowns", "questions"}
    assert kinds(built.content) == ["renewal", "unknown"]


@pytest.mark.parametrize("offset,visible", [(-1, False), (0, True), (90, True), (91, False)])
def test_renewal_window(offset: int, visible: bool) -> None:
    built = build_content(
        replace(EMPTY, renewal_date=NOW.date() + timedelta(days=offset)), policy="full", cfg=CFG
    )
    assert ("renewal" in built.content) == visible
    assert len(built.content["questions"]) == int(visible)


def test_empty_has_no_invented_questions_and_no_renewal() -> None:
    built = build_content(replace(EMPTY, last_held_at=None), policy="full", cfg=CFG)
    assert built.content["questions"] == [] and "renewal" not in built.content
    assert built.basis == "metadata" and built.evidence_ids == ()
    assert built.content["since_last_meeting"]["last_held_at"] is None


@pytest.mark.parametrize(
    "unsafe",
    [
        'send "the plan"',
        "send 'the plan'",
        "send “the plan”",
        "contact a@example.test",
        "send `the plan`",
        "send \u2018the plan\u2019",
    ],
)
def test_unsafe_statements_never_word_questions(unsafe: str) -> None:
    built = build_content(replace(EMPTY, commitments=(item(3, unsafe),)), policy="full", cfg=CFG)
    assert built.content["questions"] == []


def test_section_caps_stakeholder_boundary_and_missing_evidence() -> None:
    facts = replace(
        EMPTY,
        commitments=tuple(item(n) for n in range(10, 20)),
        complaints=(replace(item(3), evidence_ids=()),),
        stakeholders=(
            item(4, occurred_at=NOW - timedelta(days=30)),
            item(5, occurred_at=NOW + timedelta(seconds=1)),
        ),
    )
    built = build_content(facts, policy="full", cfg=CFG)
    assert len(built.content["open_commitments"]) == 5
    assert built.content["unresolved_complaints"] == []
    assert built.content["stakeholder_changes"] == []
    assert len(built.content["questions"]) == 3


def test_stakeholder_then_renewal_then_checklist_order() -> None:
    facts = replace(
        EMPTY,
        stakeholders=(item(3, occurred_at=NOW),),
        renewal_date=date(2026, 11, 1),
        unknowns=(
            {"code": "no_stated_goal", "detail": "missing"},
            {"code": "budget_unknown", "detail": "missing"},
        ),
    )
    assert kinds(build_content(facts, policy="full", cfg=CFG).content) == [
        "stakeholder",
        "renewal",
        "unknown",
    ]
    metadata = build_content(replace(facts, renewal_date=None), policy="metadata_only", cfg=CFG)
    assert [q["basis"]["ref"] for q in metadata.content["questions"]] == [
        "no_stated_goal",
        "budget_unknown",
    ]


def test_config_payload_and_routing() -> None:
    assert load_config() == CFG
    assert QUEUE_FOR_TASK["brief.meetings"] == "synth"
    assert REGISTRY["brief.meetings"].payload_model is BriefMeetingsPayload
    for payload in ({"trigger": "memory"}, {"trigger": "manual", "workspace_id": str(CLIENT)}):
        with pytest.raises(ValidationError):
            BriefMeetingsPayload.model_validate(payload)


def test_handler_is_free_and_now_is_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    class Repo:
        def __init__(self, pool: object) -> None:
            pass

        def upcoming(self, **kwargs: Any) -> list[object]:
            assert kwargs["now"] == NOW and kwargs["workspace_id"] == CLIENT
            return [object()]

        def load_facts(self, **kwargs: Any) -> MeetingFacts:
            return EMPTY

        def save(self, **kwargs: Any) -> bool:
            assert kwargs["now"] == NOW
            return True

    monkeypatch.setattr(handler, "MeetingRepo", Repo)
    ctx = cast(TaskContext, SimpleNamespace(pool=None, processing_enabled=False, llm=None))
    job = ClaimedJob(WorkspaceId(CLIENT), JobId(UUID(int=99)), "brief.meetings", 1, 3)
    result = handler.handle_brief_meetings(
        ctx, job, BriefMeetingsPayload.model_validate({"trigger": "manual"}), now=NOW
    )
    assert result == {
        "meetings": 1,
        "briefs_written": 1,
        "briefs_reworded": 0,
        "trigger": "manual",
    }


def test_sweep_enqueues_one_job_per_qualifying_workspace_per_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from firra.db.routines import RoutinesRepo
    from firra.maintenance import router as maintenance
    from firra.tasks.auth import TaskAuthenticator
    from firra_contracts import TaskEnvelope
    from tests.conftest import LOCAL_SECRET, local_settings
    from tests.tokens import local_token

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "Clock":
            return cls(2026, 10, 3, 12, tzinfo=UTC)

    class Jobs:
        def __init__(self) -> None:
            self.keys: set[str] = set()

        def create(self, **kwargs: Any) -> UUID | None:
            assert kwargs["job_type"] == "brief.meetings"
            assert kwargs["payload"] == {"trigger": "sweep"}
            key = kwargs["idempotency_key"]
            if key in self.keys:
                return None
            self.keys.add(key)
            return UUID(int=len(self.keys) + 100)

    class Queue:
        def __init__(self) -> None:
            self.envelopes: list[TaskEnvelope] = []

        def enqueue(self, envelope: TaskEnvelope) -> None:
            self.envelopes.append(envelope)

    def workspaces(pool: Any, *, now: datetime, cfg: MeetingConfig) -> list[WorkspaceId]:
        assert now == NOW and cfg == CFG
        return [WorkspaceId(CLIENT), WorkspaceId(UUID(int=9))]

    monkeypatch.setattr(maintenance, "datetime", Clock)
    monkeypatch.setattr(maintenance, "active_connections", lambda pool: [])
    monkeypatch.setattr(maintenance, "claim_stuck_jobs", lambda pool: [])
    monkeypatch.setattr(maintenance, "workspaces_with_pending", lambda pool: [])
    monkeypatch.setattr(maintenance, "workspaces_with_meetings", workspaces)
    monkeypatch.setattr(RoutinesRepo, "due_routines", lambda self, **kwargs: [])
    jobs, queue = Jobs(), Queue()
    app = FastAPI()
    app.state.task_authenticator = TaskAuthenticator(settings=local_settings(), jwks=None)  # type: ignore[arg-type]
    app.state.task_context = SimpleNamespace(pool=None, jobs=jobs, queue=queue)
    app.include_router(maintenance.router)
    headers = {"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"}
    with TestClient(app) as client:
        assert client.post("/maintenance/sweep").status_code == 401
        response = client.post("/maintenance/sweep", headers=headers)
        assert response.status_code == 200 and response.json()["briefs"] == 2
        assert {e.workspace_id for e in queue.envelopes} == {CLIENT, UUID(int=9)}
        assert all(e.type == "brief.meetings" for e in queue.envelopes)
        assert jobs.keys == {f"brief:{ws}:20261003T12q0" for ws in (CLIENT, UUID(int=9))}
        queue.envelopes.clear()
        assert client.post("/maintenance/sweep", headers=headers).json()["briefs"] == 0
        assert queue.envelopes == []
