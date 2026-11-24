"""Account memory rebuild against local Postgres, with verified fixture extractions."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from firra.app import create_app
from firra.db.memory import MemoryRepo
from firra.db.pool import create_pool_for
from firra.db.types import WorkspaceId
from firra.memory.rules import load_config
from firra.tasks.queue import LocalQueue
from firra_contracts import TaskEnvelope
from tests.conftest import LOCAL_SECRET, local_settings
from tests.tokens import local_token

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]


@pytest.fixture
def db() -> Iterator[Db]:
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as conn:
        yield conn


def _id(row: dict[str, Any] | None) -> UUID:
    if row is None:
        raise RuntimeError("fixture insert returned no id")
    return UUID(str(row["id"]))


def _count(db: Db, query: str, params: tuple[UUID, ...]) -> int:
    row = db.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError("count query returned no row")
    return int(row["n"])


def _workspace(db: Db, name: str) -> tuple[UUID, UUID]:
    workspace_id = _id(
        db.execute(
            "insert into public.workspace (name) values (%s) returning id", (name,)
        ).fetchone()
    )
    connection_id = _id(
        db.execute(
            """insert into public.connection (workspace_id, provider, mode, account_label)
            values (%s, 'email_upload', 'upload', 'fixture') returning id""",
            (workspace_id,),
        ).fetchone()
    )
    return workspace_id, connection_id


def _client(db: Db, workspace_id: UUID, name: str) -> UUID:
    return _id(
        db.execute(
            "insert into public.client (workspace_id, name, policy) "
            "values (%s, %s, 'full') returning id",
            (workspace_id, name),
        ).fetchone()
    )


def _extraction(
    db: Db,
    workspace_id: UUID,
    connection_id: UUID,
    client_id: UUID,
    external_id: str,
    kind: str,
    text: str,
    quote: str,
    at: datetime,
    payload_extra: dict[str, Any] | None = None,
    author_person_id: UUID | None = None,
) -> UUID:
    with db.transaction():
        record_id = _id(
            db.execute(
                """insert into public.source_record (workspace_id, connection_id, provider,
                  external_id, kind, client_id, policy_at_ingest, thread_key, occurred_at,
                  direction, body_clean, author_person_id, processing_state)
                values (%s, %s, 'email_upload', %s, 'email', %s, 'full', %s, %s,
                  'in', %s, %s, 'done') returning id""",
                (
                    workspace_id,
                    connection_id,
                    external_id,
                    client_id,
                    external_id,
                    at,
                    quote,
                    author_person_id,
                ),
            ).fetchone()
        )
        evidence_id = _id(
            db.execute(
                """insert into public.evidence (workspace_id, source_record_id, client_id,
                  quote, locator) values (%s, %s, %s, %s, '{"start":0,"end":20}') returning id""",
                (workspace_id, record_id, client_id, quote),
            ).fetchone()
        )
        payload: dict[str, Any] = {"text": text}
        payload.update(payload_extra or {})
        if kind == "commitment":
            payload |= {"owner_side": "client", "due_date": "2026-09-10"}
        extraction_id = _id(
            db.execute(
                """insert into public.extraction (workspace_id, source_record_id, client_id,
                  kind, payload, model, prompt_version, processing_version, item_index)
                values (%s, %s, %s, %s, %s, 'fake', 'extract.v1', 1, 0) returning id""",
                (workspace_id, record_id, client_id, kind, psycopg.types.json.Jsonb(payload)),
            ).fetchone()
        )
        db.execute(
            """insert into public.extraction_evidence (workspace_id, extraction_id,
                  evidence_id, client_id) values (%s, %s, %s, %s)""",
            (workspace_id, extraction_id, evidence_id, client_id),
        )
    return record_id


def test_rebuild_is_tenant_scoped_idempotent_and_removes_stale_commitment(db: Db) -> None:
    first_ws, first_conn = _workspace(db, "memory-one")
    other_ws, other_conn = _workspace(db, "memory-two")
    try:
        a = _client(db, first_ws, "Alpha")
        b = _client(db, first_ws, "Beta")
        other = _client(db, other_ws, "Other")
        record_id = _extraction(
            db,
            first_ws,
            first_conn,
            a,
            "a1",
            "commitment",
            "Client will send the plan.",
            "We will send the plan by Friday.",
            datetime(2026, 9, 1, tzinfo=UTC),
        )
        _extraction(
            db,
            first_ws,
            first_conn,
            b,
            "b1",
            "goal",
            "Grow qualified leads.",
            "We need more qualified leads this year.",
            datetime(2026, 9, 2, tzinfo=UTC),
        )
        _extraction(
            db,
            first_ws,
            first_conn,
            b,
            "future-goal",
            "goal",
            "Grow a new region.",
            "We should enter a new region next quarter.",
            datetime(2026, 10, 1, tzinfo=UTC),
        )
        _extraction(
            db,
            other_ws,
            other_conn,
            other,
            "other1",
            "goal",
            "Grow revenue.",
            "We need more revenue this year.",
            datetime(2026, 9, 2, tzinfo=UTC),
        )
        with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
            repo = MemoryRepo(pool)
            cfg = load_config()
            clients = repo.clients(workspace_id=WorkspaceId(first_ws))
            assert {c["id"] for c in clients} == {a, b}
            for client in clients:
                assert repo.rebuild_client(
                    workspace_id=WorkspaceId(first_ws),
                    client_id=client["id"],
                    as_of=date(2026, 9, 29),
                    cfg=cfg,
                )
            for client in clients:
                assert not repo.rebuild_client(
                    workspace_id=WorkspaceId(first_ws),
                    client_id=client["id"],
                    as_of=date(2026, 9, 30),
                    cfg=cfg,
                )
            assert (
                _count(
                    db,
                    "select count(*) as n from public.client_card where workspace_id = %s",
                    (first_ws,),
                )
                == 2
            )
            assert (
                _count(
                    db,
                    "select count(*) as n from public.client_card where workspace_id = %s",
                    (other_ws,),
                )
                == 0
            )
            status = db.execute(
                "select status::text from public.commitment "
                "where workspace_id = %s and client_id = %s",
                (first_ws, a),
            ).fetchone()
            assert status is not None and status["status"] == "late"
            assert (
                _count(
                    db,
                    "select count(*) as n from public.client_goal "
                    "where workspace_id = %s and client_id = %s",
                    (first_ws, b),
                )
                == 1
            )
            # Reprocessing dropped the quote, which cascades its evidence link.
            db.execute(
                "delete from public.extraction where workspace_id = %s and source_record_id = %s",
                (first_ws, record_id),
            )
            db.execute(
                "delete from public.evidence where workspace_id = %s and source_record_id = %s",
                (first_ws, record_id),
            )
            assert repo.rebuild_client(
                workspace_id=WorkspaceId(first_ws),
                client_id=a,
                as_of=date(2026, 9, 30),
                cfg=cfg,
            )
            assert (
                _count(
                    db,
                    "select count(*) as n from public.commitment "
                    "where workspace_id = %s and client_id = %s",
                    (first_ws, a),
                )
                == 0
            )
    finally:
        db.execute("delete from public.workspace where id in (%s, %s)", (first_ws, other_ws))


def test_request_quote_repeated_in_replies_has_two_evidence_links(db: Db) -> None:
    workspace_id, connection_id = _workspace(db, "memory-request")
    try:
        client_id = _client(db, workspace_id, "Echo")
        quote = "Please send the campaign report this week."
        for index in range(2):
            _extraction(
                db,
                workspace_id,
                connection_id,
                client_id,
                f"reply-{index}",
                "request",
                "Client asks for the campaign report.",
                quote,
                datetime(2026, 9, index + 1, tzinfo=UTC),
            )
        with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
            repo = MemoryRepo(pool)
            repo.rebuild_client(
                workspace_id=WorkspaceId(workspace_id),
                client_id=client_id,
                as_of=date(2026, 9, 29),
                cfg=load_config(),
            )
        assert (
            _count(
                db,
                "select count(*) as n from public.request "
                "where workspace_id = %s and client_id = %s",
                (workspace_id, client_id),
            )
            == 1
        )
        assert (
            _count(
                db,
                "select count(*) as n from public.request_evidence "
                "where workspace_id = %s and client_id = %s",
                (workspace_id, client_id),
            )
            == 2
        )
        earliest = db.execute(
            "select external_id from public.source_record where workspace_id = %s and id = "
            "(select source_record_id from public.request "
            "where workspace_id = %s and client_id = %s)",
            (workspace_id, workspace_id, client_id),
        ).fetchone()
        assert earliest is not None and earliest["external_id"] == "reply-0"
    finally:
        db.execute("delete from public.workspace where id = %s", (workspace_id,))


def test_result_quote_repeated_in_replies_has_one_card_entry(db: Db) -> None:
    workspace_id, connection_id = _workspace(db, "memory-result")
    try:
        client_id = _client(db, workspace_id, "Echo")
        for index in range(2):
            _extraction(
                db,
                workspace_id,
                connection_id,
                client_id,
                f"result-{index}",
                "result_reported",
                "Leads rose by 20%.",
                "Qualified leads rose by 20 percent this month.",
                datetime(2026, 9, index + 1, tzinfo=UTC),
            )
        with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
            MemoryRepo(pool).rebuild_client(
                workspace_id=WorkspaceId(workspace_id),
                client_id=client_id,
                as_of=date(2026, 9, 29),
                cfg=load_config(),
            )
        row = db.execute(
            "select content from public.client_card where workspace_id = %s and client_id = %s",
            (workspace_id, client_id),
        ).fetchone()
        assert row is not None
        assert len(row["content"]["results"]) == 1
        assert len(row["content"]["results"][0]["evidence_ids"]) == 2
    finally:
        db.execute("delete from public.workspace where id = %s", (workspace_id,))


def test_stakeholder_updates_need_verified_name_and_title(db: Db) -> None:
    workspace_id, connection_id = _workspace(db, "memory-stakeholder")
    try:
        client_id = _client(db, workspace_id, "Echo")
        person_id = _id(
            db.execute(
                "insert into public.person (workspace_id, client_id, side, display_name) "
                "values (%s, %s, 'client', 'Victor Hale') returning id",
                (workspace_id, client_id),
            ).fetchone()
        )
        _extraction(
            db,
            workspace_id,
            connection_id,
            client_id,
            "valid-title",
            "stakeholder_change",
            "Victor joined as VP Marketing.",
            "Victor Hale joined as VP Marketing this month.",
            datetime(2026, 9, 1, tzinfo=UTC),
            {"person_name": "Victor Hale", "role_title": "VP Marketing", "change": "new_role"},
        )
        _extraction(
            db,
            workspace_id,
            connection_id,
            client_id,
            "fake-person",
            "stakeholder_change",
            "Maria joined as CEO.",
            "A new executive joined the client this month.",
            datetime(2026, 9, 2, tzinfo=UTC),
            {"person_name": "Maria Santos", "role_title": "CEO", "change": "joined"},
        )
        _extraction(
            db,
            workspace_id,
            connection_id,
            client_id,
            "own-departure",
            "stakeholder_change",
            "Victor is leaving.",
            "My last day at Echo will be Friday.",
            datetime(2026, 9, 3, tzinfo=UTC),
            {"person_name": "Victor Hale", "change": "left"},
            author_person_id=person_id,
        )
        with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
            # Twice at the same date, then at an earlier date: observations stay single and
            # a rebuild never removes what lies after its own date.
            for as_of in (date(2026, 9, 29), date(2026, 9, 29), date(2026, 8, 15)):
                MemoryRepo(pool).rebuild_client(
                    workspace_id=WorkspaceId(workspace_id),
                    client_id=client_id,
                    as_of=as_of,
                    cfg=load_config(),
                )
        observations = db.execute(
            "select person_id, observed_at, source::text as source, role_title, "
            "is_decision_maker, source_record_id is not null as linked "
            "from public.person_observation where workspace_id = %s",
            (workspace_id,),
        ).fetchall()
        assert observations == [
            {
                "person_id": person_id,
                "observed_at": datetime(2026, 9, 1, tzinfo=UTC),
                "source": "stakeholder_change",
                "role_title": "VP Marketing",
                "is_decision_maker": True,
                "linked": True,
            }
        ]
        people = db.execute(
            "select display_name, role_title, is_decision_maker, departed_at "
            "from public.person where workspace_id = %s and client_id = %s",
            (workspace_id, client_id),
        ).fetchall()
        assert len(people) == 1
        assert people[0]["display_name"] == "Victor Hale"
        assert people[0]["role_title"] == "VP Marketing"
        assert people[0]["is_decision_maker"] is True
        assert people[0]["departed_at"] == datetime(2026, 9, 3, tzinfo=UTC)
    finally:
        db.execute("delete from public.workspace where id = %s", (workspace_id,))


def test_metadata_only_card_has_no_body_derived_fields(db: Db) -> None:
    workspace_id, connection_id = _workspace(db, "memory-metadata")
    try:
        client_id = _id(
            db.execute(
                "insert into public.client (workspace_id, name, policy) "
                "values (%s, 'Metadata', 'metadata_only') returning id",
                (workspace_id,),
            ).fetchone()
        )
        person_id = _id(
            db.execute(
                "insert into public.person (workspace_id, client_id, side, display_name) "
                "values (%s, %s, 'client', 'Maria Santos') returning id",
                (workspace_id, client_id),
            ).fetchone()
        )
        for index in range(3):
            db.execute(
                """insert into public.source_record (workspace_id, connection_id, provider,
                      external_id, kind, client_id, policy_at_ingest, occurred_at,
                      author_person_id, direction, processing_state)
                    values (%s, %s, 'email_upload', %s, 'email', %s, 'metadata_only',
                      %s, %s, 'in', 'not_applicable')""",
                (
                    workspace_id,
                    connection_id,
                    f"meta-{index}",
                    client_id,
                    datetime(2026, 9, index + 1, tzinfo=UTC),
                    person_id,
                ),
            )
        with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
            MemoryRepo(pool).rebuild_client(
                workspace_id=WorkspaceId(workspace_id),
                client_id=client_id,
                as_of=date(2026, 9, 29),
                cfg=load_config(),
            )
        row = db.execute(
            "select content from public.client_card where workspace_id = %s and client_id = %s",
            (workspace_id, client_id),
        ).fetchone()
        assert row is not None
        assert set(row["content"]) == {"version", "as_of", "stakeholders", "timeline", "unknowns"}
        assert row["content"]["stakeholders"][0]["is_champion"] is True
    finally:
        db.execute("delete from public.workspace where id = %s", (workspace_id,))


def test_memory_task_uses_contract_and_registry(db: Db, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_id, _ = _workspace(db, "memory-task")
    try:
        enqueued: list[TaskEnvelope] = []

        def capture_enqueue(
            _queue: LocalQueue, envelope: TaskEnvelope, *, delay_seconds: float = 0
        ) -> None:
            enqueued.append(envelope)

        monkeypatch.setattr(LocalQueue, "enqueue", capture_enqueue)
        client_id = _client(db, workspace_id, "Alpha")
        job_id = _id(
            db.execute(
                "insert into public.job (workspace_id, type, idempotency_key, payload) "
                "values (%s, 'memory.rebuild', 'memory-task-test', "
                '\'{"trigger":"sweep","as_of":"2026-09-29"}\') returning id',
                (workspace_id,),
            ).fetchone()
        )
        with TestClient(create_app(local_settings())) as client:
            response = client.post(
                "/tasks/memory.rebuild",
                headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
                json={
                    "workspace_id": str(workspace_id),
                    "job_id": str(job_id),
                    "idempotency_key": "memory-task-test",
                    "type": "memory.rebuild",
                    "payload": {"trigger": "sweep", "as_of": "2026-09-29"},
                },
            )
        assert response.json() == {"status": "succeeded"}
        row = db.execute(
            "select content from public.client_card where workspace_id = %s and client_id = %s",
            (workspace_id, client_id),
        ).fetchone()
        assert row is not None and row["content"]["as_of"] == "2026-09-29"
        follow_up = db.execute(
            "select type, payload from public.job where workspace_id = %s and idempotency_key = %s",
            (workspace_id, f"signals:{job_id}"),
        ).fetchone()
        assert follow_up is not None
        assert follow_up["type"] == "signals.evaluate"
        assert follow_up["payload"] == {"trigger": "memory", "as_of": "2026-09-29"}
        needs_follow_up = db.execute(
            "select type, payload from public.job where workspace_id = %s and idempotency_key = %s",
            (workspace_id, f"needs:{job_id}"),
        ).fetchone()
        assert needs_follow_up is not None
        assert needs_follow_up["type"] == "needs.update"
        assert needs_follow_up["payload"] == {"trigger": "memory", "as_of": "2026-09-29"}
        assert {envelope.type for envelope in enqueued} == {"signals.evaluate", "needs.update"}
    finally:
        db.execute("delete from public.workspace where id = %s", (workspace_id,))
