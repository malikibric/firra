"""Synthetic owner-AI jobs on bundled Postgres, delivered by LocalExecutor."""

import hashlib
import os
import tempfile
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.ai.executor import (
    ExecutorLimitReached,
    ExecutorNotInstalled,
    ExecutorNotSignedIn,
    FakeExecutor,
    bind_executor,
)
from firra.ai.processor import PacketProcessor
from firra.db.ai_processing import AiProcessingRepo
from firra.db.jobs import PgJobStore
from firra.db.pool import create_pool_for
from firra.db.records import RecordInput, RecordRepo
from firra.db.types import WorkspaceId
from firra.llm.prompts import load_prompt
from firra.local.executor import LocalExecutor
from firra.local.pg import PostgresSupervisor
from firra.tasks.handlers.process import request_processing
from firra_contracts import TaskEnvelope
from tests.unit.ai.test_processor import BODY, answer, item
from tests.unit.local.test_executor import context, until

pytestmark = pytest.mark.integration
PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")


@pytest.fixture
def world() -> Iterator[dict[str, Any]]:
    if not PG_BIN:
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    with tempfile.TemporaryDirectory(prefix="firra-ai-", dir="/tmp") as root:
        pg = PostgresSupervisor(Path(PG_BIN), Path(root) / "data", Path(root) / "run")
        try:
            database = pg.ensure_ready()
            with (
                psycopg.connect(database, row_factory=dict_row, autocommit=True) as db,
                create_pool_for(database) as pool,
            ):

                def insert(query: str, params: tuple[Any, ...] = ()) -> UUID:
                    row = db.execute(query, params).fetchone()
                    assert row is not None
                    return UUID(str(row["id"]))

                ws = WorkspaceId(
                    insert("insert into public.workspace (name) values ('AI fixture') returning id")
                )
                client_id = insert(
                    "insert into public.client (workspace_id,name,policy) "
                    "values (%s,'fixture','full') returning id",
                    (ws,),
                )
                connection_id = insert(
                    "insert into public.connection (workspace_id,provider,mode,account_label) "
                    "values (%s,'email_upload','upload','fixture') returning id",
                    (ws,),
                )
                repo = RecordRepo(pool)
                inputs = [
                    RecordInput(
                        connection_id=connection_id,
                        provider="email_upload",
                        external_id=str(n),
                        kind="email",
                        client_id=client_id,
                        policy="full",
                        thread_key=str(n),
                        occurred_at=datetime(2026, 10, 5, n, tzinfo=UTC),
                        direction="in",
                        word_count=len(BODY.split()),
                        metadata={"subject": "fixture"},
                        body_clean=BODY,
                        raw_object_uri=None,
                        deep_link=None,
                        content_hash=hashlib.sha256(BODY.encode()).digest(),
                    )
                    for n in (1, 2)
                ]
                ids = [
                    repo.upsert_record(
                        workspace_id=ws, record=record, author_person_id=None, participants=[]
                    )[0]
                    for record in inputs
                ]
                yield {
                    "db": db,
                    "pool": pool,
                    "ws": ws,
                    "client": client_id,
                    "ids": ids,
                    "inputs": inputs,
                    "records": repo,
                }
        finally:
            pg.stop()


def envelope(ws: WorkspaceId) -> TaskEnvelope:
    return TaskEnvelope(
        workspace_id=ws,
        job_id=uuid4(),
        idempotency_key=str(uuid4()),
        type="process.records",
        payload={"trigger": "sweep"},
    )


def job(world: dict[str, Any], env: TaskEnvelope) -> dict[str, Any]:
    row = (
        world["db"]
        .execute(
            "select status,error_class,progress,attempts from public.job "
            "where workspace_id=%s and id=%s",
            (world["ws"], env.job_id),
        )
        .fetchone()
    )
    assert row is not None
    return dict(row)


def test_executor_packet_persists_verified_rows_and_second_run_is_noop(
    world: dict[str, Any],
) -> None:
    pool, ws = world["pool"], world["ws"]
    executor = FakeExecutor(
        [
            answer(
                ("r1", [item(kind="request")]),
                ("r2", [item(kind="request")]),
            )
        ]
    )
    bind_executor(pool, executor)
    runner = LocalExecutor(replace(context(PgJobStore(pool)), pool=pool, processing_enabled=True))
    env = envelope(ws)
    try:
        runner.enqueue(env)
        runner.start()
        until(lambda: job(world, env)["status"] == "succeeded")
        assert len(executor.calls) == 1
        assert executor.calls[0][1].index('id="r1"') < executor.calls[0][1].index('id="r2"')
        pending = AiProcessingRepo(pool).pending(workspace_id=ws, limit=20)
        assert not pending
        rows = (
            world["db"]
            .execute(
                "select x.source_record_id,x.model,x.prompt_version,x.payload,e.quote "
                "from public.extraction x join public.extraction_evidence link "
                "on link.workspace_id=x.workspace_id and link.extraction_id=x.id "
                "join public.evidence e on e.workspace_id=link.workspace_id "
                "and e.id=link.evidence_id "
                "where x.workspace_id=%s",
                (ws,),
            )
            .fetchall()
        )
        assert len(rows) == 2 and {r["source_record_id"] for r in rows} == set(world["ids"])
        assert all(
            (r["model"], r["prompt_version"], r["quote"]) == ("codex", "extract.v6", BODY)
            for r in rows
        )
        assert all(r["payload"]["topic_key"] == "attribution-reporting" for r in rows)
        assert len({r["payload"]["_provenance"]["packet_id"] for r in rows}) == 1
        progress = job(world, env)["progress"]
        assert progress["items"] == 2 and progress["input_tokens"] == 20
        usage = (
            world["db"]
            .execute(
                "select count(*) as n from public.llm_usage where workspace_id=%s",
                (ws,),
            )
            .fetchone()
        )
        assert usage is not None and usage["n"] == 0
        assert request_processing(runner.context, ws, "sweep") is False
        again = envelope(ws)
        runner.enqueue(again)
        until(lambda: job(world, again)["status"] == "succeeded")
        assert job(world, again)["progress"]["extracted"] == 0
        assert len(executor.calls) == 1
    finally:
        runner.stop(wait=3)


def test_retry_counter_scope_stale_snapshot_park_and_changed_ingest_reset(
    world: dict[str, Any],
) -> None:
    pool, ws = world["pool"], world["ws"]
    repo = AiProcessingRepo(pool)
    records = repo.pending(workspace_id=ws, limit=20)
    assert [r.id for r in records] == list(reversed(world["ids"]))
    source = records[1]
    assert repo.attempt(workspace_id=WorkspaceId(uuid4()), record=source) is None
    assert repo.attempt(workspace_id=ws, record=replace(source, content_hash=b"stale")) is None
    assert [repo.attempt(workspace_id=ws, record=source) for _ in range(3)] == [
        "pending",
        "pending",
        "error",
    ]
    records_repo = world["records"]
    original = world["inputs"][0]
    records_repo.upsert_record(
        workspace_id=ws, record=original, author_person_id=None, participants=[]
    )
    row = (
        world["db"]
        .execute(
            "select processing_attempts,processing_state from public.source_record "
            "where workspace_id=%s and id=%s",
            (ws, source.id),
        )
        .fetchone()
    )
    assert row == {"processing_attempts": 3, "processing_state": "error"}
    new_body = "We need an edited attribution report for next quarter."
    edited = replace(
        original, body_clean=new_body, content_hash=hashlib.sha256(new_body.encode()).digest()
    )
    records_repo.upsert_record(
        workspace_id=ws, record=edited, author_person_id=None, participants=[]
    )
    row = (
        world["db"]
        .execute(
            "select processing_attempts,processing_state from public.source_record "
            "where workspace_id=%s and id=%s",
            (ws, source.id),
        )
        .fetchone()
    )
    assert row == {"processing_attempts": 0, "processing_state": "pending"}
    assert repo.attempt(workspace_id=ws, record=source) is None
    assert (
        repo.save(
            workspace_id=ws,
            record=source,
            cleaned_body=None,
            items=[],
            model="codex",
            prompt_version="extract.v6",
        )
        is False
    )


def test_failed_packet_attempts_are_atomic_with_job_progress(world: dict[str, Any]) -> None:
    pool, ws = world["pool"], world["ws"]
    repo = AiProcessingRepo(pool)
    store = PgJobStore(pool)
    env = envelope(ws)
    job_id = store.create(
        workspace_id=ws, job_type=env.type, idempotency_key=env.idempotency_key, payload=env.payload
    )
    assert job_id is not None
    claimed = store.claim(workspace_id=ws, job_id=job_id, job_type=env.type)
    assert claimed is not None
    with (
        pytest.raises(RuntimeError, match="ownership lost"),
        repo.record_progress(job=replace(claimed, attempts=0), progress=lambda: {"errors": 1}),
    ):
        repo.attempt(workspace_id=ws, record=repo.pending(workspace_id=ws, limit=1)[0])
    rows = (
        world["db"]
        .execute(
            "select processing_attempts from public.source_record where workspace_id=%s",
            (ws,),
        )
        .fetchall()
    )
    assert all(r["processing_attempts"] == 0 for r in rows)


def test_policy_flip_stores_nothing_on_real_repository(world: dict[str, Any]) -> None:
    class FlipExecutor(FakeExecutor):
        def run(self, *args: Any, **kwargs: Any) -> Any:
            world["db"].execute(
                "update public.client set policy='excluded' where workspace_id=%s and id=%s",
                (world["ws"], world["client"]),
            )
            return super().run(*args, **kwargs)

    repo = AiProcessingRepo(world["pool"])
    processor = PacketProcessor(
        FlipExecutor([answer(("r1", [item(kind="request")]), ("r2", []))]),
        repo,
        load_prompt("extract.v6"),
        world["ws"],
    )
    assert processor.process(repo.pending(workspace_id=world["ws"], limit=20)).changed == 2
    rows = (
        world["db"]
        .execute(
            "select count(*) as n from public.extraction where workspace_id=%s",
            (world["ws"],),
        )
        .fetchone()
    )
    assert rows is not None and rows["n"] == 0


@pytest.mark.parametrize(
    "failure", [ExecutorNotInstalled(), ExecutorNotSignedIn(), ExecutorLimitReached()]
)
def test_executor_errors_fail_or_delay_real_jobs(world: dict[str, Any], failure: Any) -> None:
    pool, ws = world["pool"], world["ws"]
    fake = FakeExecutor([failure])
    if isinstance(failure, ExecutorLimitReached):
        failure.resume_at = datetime.now(UTC) + timedelta(seconds=600)
    bind_executor(pool, fake)
    runner = LocalExecutor(replace(context(PgJobStore(pool)), pool=pool, processing_enabled=True))
    env = envelope(ws)
    try:
        runner.enqueue(env)
        runner.start()
        if isinstance(failure, ExecutorLimitReached):
            until(
                lambda: (
                    job(world, env)["status"] == "queued"
                    and job(world, env)["attempts"] == 0
                    and len(fake.calls) == 1
                )
            )
            until(lambda: not runner._active)
            assert runner._heap[0][0] - runner.clock() > 590
        else:
            until(lambda: job(world, env)["status"] == "failed")
            assert job(world, env)["error_class"] == failure.error_class
        rows = (
            world["db"]
            .execute(
                "select processing_state,processing_attempts from public.source_record "
                "where workspace_id=%s",
                (ws,),
            )
            .fetchall()
        )
        assert all(r == {"processing_state": "pending", "processing_attempts": 0} for r in rows)
    finally:
        runner.stop(wait=3)
