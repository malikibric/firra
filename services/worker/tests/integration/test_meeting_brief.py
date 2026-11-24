"""Meeting persistence against local Supabase; fixture data only, never a remote database."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.brief.meeting import BriefItem, BuiltBrief, MeetingConfig, MeetingFacts, build_content
from firra.db.meetings import Meeting, MeetingRepo, workspaces_with_meetings
from firra.db.pool import create_pool_for
from firra.db.types import WorkspaceId
from tests.conftest import local_settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
CFG = MeetingConfig()
Db = psycopg.Connection[dict[str, Any]]


@pytest.fixture
def db() -> Iterator[Db]:
    try:
        conn = psycopg.connect(
            local_settings().database_url.get_secret_value(),
            row_factory=dict_row,
            autocommit=True,
            connect_timeout=2,
        )
    except psycopg.OperationalError:
        pytest.skip("local Supabase Postgres is unavailable")
    with conn:
        schema = conn.execute("select to_regclass('public.meeting_brief') as table").fetchone()
        if schema is None or schema["table"] is None:
            pytest.skip("local Supabase lacks T45a migration 27")
        yield conn


@pytest.fixture
def pool(db: Db) -> Iterator[ConnectionPool[Db]]:
    with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
        yield pool


@pytest.fixture
def world(db: Db) -> Iterator[dict[str, UUID]]:
    ids = {k: uuid4() for k in ("ws", "client", "connection", "person")}
    db.execute(
        "insert into public.workspace (id, name) values (%s, 'meeting fixture')", (ids["ws"],)
    )
    db.execute(
        "insert into public.client (id, workspace_id, name) values (%s, %s, 'client')",
        (ids["client"], ids["ws"]),
    )
    db.execute(
        """insert into public.connection
        (id, workspace_id, provider, mode, account_label)
        values (%s, %s, 'email_upload', 'upload', 'fixture')""",
        (ids["connection"], ids["ws"]),
    )
    db.execute(
        """insert into public.person
        (id, workspace_id, client_id, side, first_seen_at)
        values (%s, %s, %s, 'client', %s)""",
        (ids["person"], ids["ws"], ids["client"], NOW - timedelta(days=100)),
    )
    yield ids
    db.execute("delete from public.workspace where id = %s", (ids["ws"],))


def record(db: Db, world: dict[str, UUID], at: datetime, *, client: UUID | None = None) -> UUID:
    ident = uuid4()
    db.execute(
        """insert into public.source_record
        (id, workspace_id, connection_id, provider, external_id, kind, client_id,
         policy_at_ingest, occurred_at, direction, author_person_id, thread_key)
        values (%s, %s, %s, 'email_upload', %s, 'email', %s, 'full', %s, 'in', %s, %s)""",
        (
            ident,
            world["ws"],
            world["connection"],
            str(ident),
            client or world["client"],
            at,
            world["person"],
            str(ident),
        ),
    )
    return ident


def event(db: Db, world: dict[str, UUID], at: datetime, status: str = "scheduled") -> Meeting:
    source = record(db, world, NOW - timedelta(days=1))
    ident = uuid4()
    db.execute(
        """insert into public.calendar_event
        (id, workspace_id, source_record_id, client_id, starts_at,
         duration_min, status, attendee_count)
        values (%s, %s, %s, %s, %s, 30, %s, 2)""",
        (ident, world["ws"], source, world["client"], at, status),
    )
    return Meeting(ident, world["client"], at)


def extraction(
    db: Db, world: dict[str, UUID], kind: str, text: str, at: datetime
) -> tuple[UUID, UUID]:
    source = record(db, world, at)
    evidence, ident = uuid4(), uuid4()
    db.execute(
        """insert into public.evidence
        (id, workspace_id, client_id, source_record_id, quote, locator)
        values (%s, %s, %s, %s, 'fixture evidence quote', '{}')""",
        (evidence, world["ws"], world["client"], source),
    )
    with db.transaction():
        db.execute(
            """insert into public.extraction
            (id, workspace_id, client_id, source_record_id, kind, payload, model, prompt_version,
             processing_version, item_index)
            values (%s, %s, %s, %s, %s, %s, 'fixture', 'fixture', 1, 0)""",
            (
                ident,
                world["ws"],
                world["client"],
                source,
                kind,
                Jsonb({"text": text, "subtype": "delay"}),
            ),
        )
        db.execute(
            """insert into public.extraction_evidence
            (workspace_id, client_id, extraction_id, evidence_id) values (%s, %s, %s, %s)""",
            (world["ws"], world["client"], ident, evidence),
        )
    return ident, evidence


def test_retry_reschedule_facts_and_evidence(
    db: Db, pool: ConnectionPool[Db], world: dict[str, UUID]
) -> None:
    held = event(db, world, NOW - timedelta(days=10), "held")
    meeting = event(db, world, NOW + timedelta(hours=1))
    complaint, evidence = extraction(
        db, world, "complaint", "delivery is late", NOW - timedelta(days=2)
    )
    extraction(db, world, "stakeholder_change", "a stakeholder joined", NOW - timedelta(days=1))
    with db.transaction():
        db.execute(
            """insert into public.commitment
            (workspace_id, client_id, owner_side, text, due_date, stated_at, dedupe_key)
            values (%s, %s, 'firm', 'send the plan', %s, %s, 'fixture') returning id""",
            (world["ws"], world["client"], NOW.date() - timedelta(days=1), NOW - timedelta(days=2)),
        )
        row = db.execute(
            "select id from public.commitment where workspace_id = %s", (world["ws"],)
        ).fetchone()
        assert row is not None
        db.execute(
            """insert into public.commitment_evidence
            (workspace_id, client_id, commitment_id, evidence_id) values (%s, %s, %s, %s)""",
            (world["ws"], world["client"], row["id"], evidence),
        )
    record(db, world, NOW - timedelta(days=20))
    record(db, world, NOW + timedelta(days=1))
    ws = WorkspaceId(world["ws"])
    repo = MeetingRepo(pool)
    facts = repo.load_facts(workspace_id=ws, meeting=meeting, now=NOW, cfg=CFG)
    assert facts is not None and facts.last_held_at == held.starts_at and facts.records == 4
    assert facts.complaints[0].id == complaint and facts.commitments[0].late
    assert facts.stakeholders[0].text == "a stakeholder joined"
    assert repo.save(workspace_id=ws, meeting=meeting, facts=facts, now=NOW, cfg=CFG)
    assert not repo.save(workspace_id=ws, meeting=meeting, facts=facts, now=NOW, cfg=CFG)
    assert repo.upcoming(workspace_id=ws, now=NOW, cfg=CFG) == []
    brief = db.execute(
        "select * from public.meeting_brief where workspace_id = %s", (ws,)
    ).fetchone()
    assert brief is not None and brief["basis"] == "evidence" and brief["version"] == "facts.v1"
    assert brief["model"] is None and brief["prompt_version"] is None and brief["locale"] == "en"
    assert "fixture evidence quote" not in str(brief["content"])
    links = db.execute(
        "select evidence_id from public.meeting_brief_evidence where workspace_id = %s", (ws,)
    ).fetchall()
    assert evidence in {r["evidence_id"] for r in links}
    later = replace(meeting, starts_at=NOW + timedelta(minutes=90))
    db.execute(
        "update public.calendar_event set starts_at = %s where id = %s",
        (later.starts_at, meeting.id),
    )
    assert repo.upcoming(workspace_id=ws, now=NOW, cfg=CFG) == [later]
    assert not repo.save(workspace_id=ws, meeting=meeting, facts=facts, now=NOW, cfg=CFG)
    assert repo.save(workspace_id=ws, meeting=later, facts=facts, now=NOW, cfg=CFG)


@pytest.mark.parametrize(
    "status,offset,policy,client_status,selected",
    [
        ("scheduled", 1, "full", "active", True),
        ("scheduled", 120, "metadata_only", "active", True),
        ("scheduled", 0, "full", "active", False),
        ("scheduled", 121, "full", "active", False),
        ("cancelled", 60, "full", "active", False),
        ("rescheduled", 60, "full", "active", False),
        ("held", 60, "full", "active", False),
        ("scheduled", 60, "excluded", "active", False),
        ("scheduled", 60, "full", "churned", False),
    ],
)
def test_selection(
    db: Db,
    pool: ConnectionPool[Db],
    world: dict[str, UUID],
    status: str,
    offset: int,
    policy: str,
    client_status: str,
    selected: bool,
) -> None:
    meeting = event(db, world, NOW + timedelta(minutes=offset), status)
    db.execute(
        "update public.client set policy = %s, status = %s where id = %s",
        (policy, client_status, world["client"]),
    )
    ws = WorkspaceId(world["ws"])
    repo = MeetingRepo(pool)
    assert repo.upcoming(workspace_id=ws, now=NOW, cfg=CFG) == ([meeting] if selected else [])
    assert (ws in workspaces_with_meetings(pool, now=NOW, cfg=CFG)) == selected
    facts = MeetingFacts(meeting.client_id, NOW, 0, None)
    assert repo.save(workspace_id=ws, meeting=meeting, facts=facts, now=NOW, cfg=CFG) == selected
    assert repo.upcoming(workspace_id=WorkspaceId(uuid4()), now=NOW, cfg=CFG) == []


def test_policy_rechecked_on_write(
    db: Db, pool: ConnectionPool[Db], world: dict[str, UUID]
) -> None:
    meeting = event(db, world, NOW + timedelta(hours=1))
    _, evidence = extraction(db, world, "complaint", "PRIVATE STATEMENT", NOW)
    facts = MeetingFacts(
        meeting.client_id,
        NOW,
        3,
        None,
        complaints=(BriefItem(uuid4(), "PRIVATE STATEMENT", (evidence,)),),
    )
    db.execute(
        "update public.client set policy = 'metadata_only' where id = %s", (world["client"],)
    )
    assert MeetingRepo(pool).save(
        workspace_id=WorkspaceId(world["ws"]), meeting=meeting, facts=facts, now=NOW, cfg=CFG
    )
    row = db.execute(
        "select basis, content from public.meeting_brief where workspace_id = %s", (world["ws"],)
    ).fetchone()
    assert row is not None and row["basis"] == "metadata"
    assert "PRIVATE STATEMENT" not in str(row["content"])
    assert (
        db.execute(
            "select id from public.meeting_brief_evidence where workspace_id = %s", (world["ws"],)
        ).fetchall()
        == []
    )


def test_wrong_client_evidence_rolls_back_entire_brief(
    db: Db, pool: ConnectionPool[Db], world: dict[str, UUID]
) -> None:
    meeting = event(db, world, NOW + timedelta(hours=1))
    other = uuid4()
    db.execute(
        "insert into public.client (id, workspace_id, name) values (%s, %s, 'other')",
        (other, world["ws"]),
    )
    source = record(db, world, NOW, client=other)
    evidence = uuid4()
    db.execute(
        """insert into public.evidence
        (id, workspace_id, client_id, source_record_id, quote, locator)
        values (%s, %s, %s, %s, 'other client quote', '{}')""",
        (evidence, world["ws"], other, source),
    )
    facts = MeetingFacts(
        meeting.client_id,
        NOW,
        0,
        None,
        complaints=(BriefItem(uuid4(), "delivery is late", (evidence,)),),
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        MeetingRepo(pool).save(
            workspace_id=WorkspaceId(world["ws"]), meeting=meeting, facts=facts, now=NOW, cfg=CFG
        )
    assert (
        db.execute(
            "select id from public.meeting_brief where workspace_id = %s", (world["ws"],)
        ).fetchall()
        == []
    )


def test_missing_evidence_falls_back_to_metadata(
    db: Db, pool: ConnectionPool[Db], world: dict[str, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    from firra.db import meetings

    meeting = event(db, world, NOW + timedelta(hours=1))
    facts = MeetingFacts(meeting.client_id, NOW, 1, None)

    def faulty_builder(facts: MeetingFacts, *, policy: Any, cfg: MeetingConfig) -> BuiltBrief:
        if policy == "full":
            return BuiltBrief({"open_commitments": [{"text": "PRIVATE STATEMENT"}]}, "evidence", ())
        return build_content(facts, policy=policy, cfg=cfg)

    monkeypatch.setattr(meetings, "build_content", faulty_builder)
    assert MeetingRepo(pool).save(
        workspace_id=WorkspaceId(world["ws"]), meeting=meeting, facts=facts, now=NOW, cfg=CFG
    )
    row = db.execute(
        "select basis, content from public.meeting_brief where workspace_id = %s", (world["ws"],)
    ).fetchone()
    assert row is not None and row["basis"] == "metadata"
    assert "PRIVATE STATEMENT" not in str(row["content"])


def test_reword_replaces_drafts_once_and_keeps_the_basis(
    db: Db, pool: ConnectionPool[Db], world: dict[str, UUID]
) -> None:
    meeting = event(db, world, NOW + timedelta(hours=1))
    extraction(db, world, "complaint", "delivery is late", NOW - timedelta(days=2))
    ws = WorkspaceId(world["ws"])
    repo = MeetingRepo(pool)
    facts = repo.load_facts(workspace_id=ws, meeting=meeting, now=NOW, cfg=CFG)
    assert facts is not None and repo.save(
        workspace_id=ws, meeting=meeting, facts=facts, now=NOW, cfg=CFG
    )
    stored = repo.stored_for_wording(workspace_id=ws, meeting=meeting)
    assert stored is not None and stored.client_name == "client" and stored.questions
    questions = [{**q, "text": "Has the late delivery been resolved?"} for q in stored.questions]
    assert repo.reword(
        workspace_id=ws,
        brief=stored,
        questions=questions,
        model="test-model",
        prompt_version="meeting.v1",
    )
    row = db.execute(
        "select content, model, prompt_version from public.meeting_brief where workspace_id = %s",
        (ws,),
    ).fetchone()
    assert row is not None and row["model"] == "test-model"
    assert row["prompt_version"] == "meeting.v1"
    assert [q["text"] for q in row["content"]["questions"]] == [q["text"] for q in questions]
    assert [q["basis"] for q in row["content"]["questions"]] == [q["basis"] for q in questions]
    # The stored questions are no longer the drafts that were worded: a second write is refused.
    assert not repo.reword(
        workspace_id=ws,
        brief=stored,
        questions=stored.questions,
        model="test-model",
        prompt_version="meeting.v1",
    )


def test_reword_is_refused_after_the_policy_drops(
    db: Db, pool: ConnectionPool[Db], world: dict[str, UUID]
) -> None:
    meeting = event(db, world, NOW + timedelta(hours=1))
    extraction(db, world, "complaint", "delivery is late", NOW - timedelta(days=2))
    ws = WorkspaceId(world["ws"])
    repo = MeetingRepo(pool)
    facts = repo.load_facts(workspace_id=ws, meeting=meeting, now=NOW, cfg=CFG)
    assert facts is not None and repo.save(
        workspace_id=ws, meeting=meeting, facts=facts, now=NOW, cfg=CFG
    )
    stored = repo.stored_for_wording(workspace_id=ws, meeting=meeting)
    assert stored is not None
    db.execute(
        "update public.client set policy = 'metadata_only' where id = %s", (world["client"],)
    )
    assert repo.stored_for_wording(workspace_id=ws, meeting=meeting) is None
    assert not repo.reword(
        workspace_id=ws,
        brief=stored,
        questions=stored.questions,
        model="test-model",
        prompt_version="meeting.v1",
    )
