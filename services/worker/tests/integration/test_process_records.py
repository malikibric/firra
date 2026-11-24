"""process.records end to end on the local Postgres with a scripted model (no Vertex calls)."""

import dataclasses
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from firra.app import create_app
from firra.db.evals import EvalRepo
from firra.db.llm_usage import LlmUsageRepo
from firra.db.processing import ProcessingRepo
from firra.db.records import RecordInput, RecordRepo
from firra.db.types import WorkspaceId
from firra.http import ProviderError
from firra.llm.models import ModelSet
from firra.llm.service import Llm
from firra.llm.vertex import Embedding, Generation, Usage
from firra.tasks.queue import LocalQueue, LocalTaskFailedError
from firra_contracts import TaskEnvelope
from tests.conftest import LOCAL_SECRET, local_settings
from tests.tokens import local_token

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]

MODELS = ModelSet(
    location="eu", extract="gemini-3.5-flash-lite", synth=None, embed=None, synthdata=None
)
JANE, ANA = "jane@acme.com", "ana@northbeam.agency"
LATE = "We are two weeks late on the Q3 report and the board meets on Friday."
LEGACY_BODY = (
    f"{LATE}\n\n"
    "On Tue, Sep 1, 2026 at 10:00 AM Ana <ana@northbeam.agency> wrote:\n"
    "> Here is the draft, quoted history that must not be analyzed."
)
PROMISE = "We will send the attribution report by Wednesday."


def _items(*items: dict[str, Any]) -> str:
    return json.dumps({"items": list(items)})


# Target body marker -> model answer.
ANSWERS = {
    "two weeks late": _items(
        {
            "kind": "complaint",
            "quote": "We are two weeks late on the Q3 report",
            "speaker": JANE,
            "text": "Client says the Q3 report is two weeks late.",
            "subtype": "delay",
        },
        {
            "kind": "tone",
            "quote": "We are two weeks late on the Q3 report",
            "speaker": JANE,
            "text": "Negative tone.",
            "tone": "negative",
        },
        {
            "kind": "praise",
            "quote": "We love everything you did this quarter",
            "speaker": JANE,
            "text": "Invented praise.",
        },
    ),
    "attribution report": _items(
        {
            "kind": "commitment",
            "quote": PROMISE,
            "speaker": ANA,
            "text": "Firm promises the attribution report by Wednesday.",
            "owner_side": "firm",
            "due_date": "2026-09-02",
        },
        {
            "kind": "tone",
            "quote": PROMISE,
            "speaker": ANA,
            "text": "Positive tone.",
            "tone": "positive",
        },
    ),
    "BROKEN": "not json",
}


@dataclasses.dataclass
class ScriptedBackend:
    calls: list[str] = dataclasses.field(default_factory=list)

    def generate(self, model: str, *, user: str, **_: Any) -> Generation:
        target = user.split('<source_record id="target">', 1)[1]
        self.calls.append(target)
        text = next((a for marker, a in ANSWERS.items() if marker in target), _items())
        return Generation(text=text, usage=Usage(input_tokens=500, output_tokens=50))

    def embed(self, model: str, text: str, *, task_type: str, dimensionality: int) -> Embedding:
        raise AssertionError("extraction does not embed")


class CapturingQueue:
    def __init__(self) -> None:
        self.envelopes: list[Any] = []

    def enqueue(self, envelope: Any, *, delay_seconds: float = 0) -> None:
        self.envelopes.append(envelope)


@pytest.fixture
def db() -> Iterator[Db]:
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as conn:
        yield conn


def _id(row: dict[str, Any] | None) -> UUID:
    assert row is not None
    return UUID(str(row["id"]))


@pytest.fixture
def world(db: Db) -> Iterator[dict[str, Any]]:
    ws = _id(
        db.execute("insert into public.workspace (name) values ('proc') returning id").fetchone()
    )
    ids: dict[str, Any] = {"ws": ws}
    for key, policy in (("acme", "full"), ("globex", "metadata_only")):
        ids[key] = _id(
            db.execute(
                "insert into public.client (workspace_id, name, policy) values (%s, %s, %s) "
                "returning id",
                (ws, key.title(), policy),
            ).fetchone()
        )
    ids["conn"] = _id(
        db.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label) "
            "values (%s, 'email_upload', 'upload', 'x') returning id",
            (ws,),
        ).fetchone()
    )
    for key, email, side, client in (
        ("jane", JANE, "client", ids["acme"]),
        ("ana", ANA, "firm", None),
    ):
        ids[key] = _id(
            db.execute(
                "insert into public.person (workspace_id, client_id, side) values (%s, %s, %s) "
                "returning id",
                (ws, client, side),
            ).fetchone()
        )
        db.execute(
            "insert into public.person_identity (workspace_id, person_id, kind, value) "
            "values (%s, %s, 'email', %s)",
            (ws, ids[key], email),
        )
    yield ids
    db.execute("delete from public.workspace where id = %s", (ws,))


def _record(
    db: Db,
    world: dict[str, Any],
    key: str,
    *,
    body: str | None,
    minutes: int,
    thread: str = "t1",
    author: str = "jane",
    direction: str = "in",
    client: str = "acme",
    policy: str = "full",
    content_hash: bytes | None = None,
) -> UUID:
    return _id(
        db.execute(
            "insert into public.source_record (workspace_id, connection_id, provider, external_id, "
            "kind, client_id, policy_at_ingest, thread_key, occurred_at, author_person_id, "
            "direction, participants, body_clean, deep_link, content_hash, processing_state) "
            "values (%s, %s, 'email_upload', %s, 'email', %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, 'pending') returning id",
            (
                world["ws"],
                world["conn"],
                key,
                world[client],
                policy,
                thread,
                datetime(2026, 9, 1, 10, tzinfo=UTC) + timedelta(minutes=minutes),
                world[author],
                direction,
                [world["jane"], world["ana"]],
                body,
                f"https://mail.example/{key}",
                content_hash,
            ),
        ).fetchone()
    )


def _run(db: Db, world: dict[str, Any], backend: ScriptedBackend) -> dict[str, Any]:
    job = _id(
        db.execute(
            "insert into public.job (workspace_id, type, idempotency_key, payload) "
            "values (%s, 'process.records', %s, '{\"trigger\": \"sweep\"}') returning id",
            (world["ws"], f"process-{uuid4()}"),
        ).fetchone()
    )
    app = create_app(local_settings())
    with TestClient(app) as client:
        context = app.state.task_context
        llm = Llm(backend=backend, models=MODELS, usage=LlmUsageRepo(context.pool))
        app.state.task_context = dataclasses.replace(
            context, llm=llm, queue=CapturingQueue(), processing_enabled=True
        )
        response = client.post(
            "/tasks/process.records",
            headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
            json={
                "workspace_id": str(world["ws"]),
                "job_id": str(job),
                "idempotency_key": "k",
                "type": "process.records",
                "payload": {"trigger": "sweep"},
            },
        )
    assert response.json() == {"status": "succeeded"}
    row = db.execute("select progress from public.job where id = %s", (job,)).fetchone()
    assert row is not None
    progress: dict[str, Any] = row["progress"]
    return progress


def _state(db: Db, record_id: UUID) -> dict[str, Any]:
    row = db.execute(
        "select processing_state::text as state, processing_version, body_clean "
        "from public.source_record where id = %s",
        (record_id,),
    ).fetchone()
    assert row is not None
    return row


def test_pending_records_are_extracted_verified_and_stored(db: Db, world: dict[str, Any]) -> None:
    digest = hashlib.sha256(b"same").digest()
    late = _record(db, world, "late", body=LEGACY_BODY, minutes=0, content_hash=digest)
    resent = _record(db, world, "resent", body=LEGACY_BODY, minutes=5, content_hash=digest)
    promise = _record(
        db, world, "promise", body=PROMISE, minutes=10, thread="t2", author="ana", direction="out"
    )
    broken = _record(db, world, "broken", body="BROKEN answer please", minutes=15, thread="t3")
    purged = _record(db, world, "purged", body=None, minutes=20, thread="t4")
    metadata = _record(
        db,
        world,
        "meta",
        body=None,
        minutes=25,
        thread="t5",
        client="globex",
        policy="metadata_only",
    )
    backend = ScriptedBackend()

    progress = _run(db, world, backend)

    assert {k: progress[k] for k in progress if k not in ("completed_at", "trigger")} == {
        "extracted": 2,
        "items": 3,
        "empty": 0,
        "deduplicated": 1,
        "not_applicable": 1,
        "skipped_purged": 1,
        "errors": 1,
        "changed": 0,
        "dropped": {"quote_not_found": 1, "tone_not_client": 1},
    }
    assert _state(db, late)["state"] == "done"
    assert _state(db, resent)["state"] == "done"
    assert _state(db, promise)["state"] == "done"
    assert _state(db, broken)["state"] == "error"
    assert _state(db, purged)["state"] == "skipped_purged"
    assert _state(db, metadata)["state"] == "not_applicable"

    # The legacy body was re-cleaned; the quoted history never reached the model.
    assert _state(db, late)["body_clean"] == LATE
    assert _state(db, late)["processing_version"] == 1
    assert all("quoted history" not in call for call in backend.calls)
    assert len(backend.calls) == 4  # late, promise, broken twice (one retry)

    rows = db.execute(
        "select x.kind::text as kind, x.payload, x.prompt_version, x.model, e.quote, e.locator, "
        "e.deep_link, r.body_clean from public.extraction x "
        "join public.extraction_evidence xe on xe.extraction_id = x.id "
        "join public.evidence e on e.id = xe.evidence_id "
        "join public.source_record r on r.id = x.source_record_id "
        "where x.workspace_id = %s order by x.kind",
        (world["ws"],),
    ).fetchall()
    assert [(r["kind"], r["quote"]) for r in rows] == [
        ("commitment", PROMISE),
        ("complaint", "We are two weeks late on the Q3 report"),
        ("tone", "We are two weeks late on the Q3 report"),
    ]
    for row in rows:
        locator = row["locator"]
        assert row["body_clean"][locator["start"] : locator["end"]] == row["quote"]
        assert (row["prompt_version"], row["model"]) == ("extract.v5", "gemini-3.5-flash-lite")
        assert row["deep_link"].startswith("https://mail.example/")
    assert rows[0]["payload"]["due_date"] == "2026-09-02"
    assert rows[1]["payload"] == {
        "speaker": JANE,
        "text": "Client says the Q3 report is two weeks late.",
        "subtype": "delay",
    }
    # Complaint and tone share one evidence row.
    evidence = db.execute(
        "select count(*) as n from public.evidence where workspace_id = %s", (world["ws"],)
    ).fetchone()
    assert evidence is not None and evidence["n"] == 2

    usage = db.execute(
        "select count(*) as n from public.llm_usage where workspace_id = %s", (world["ws"],)
    ).fetchone()
    assert usage is not None and usage["n"] == 4

    # Nothing is pending any more: a second run makes no calls.
    again = ScriptedBackend()
    assert _run(db, world, again)["extracted"] == 0
    assert again.calls == []


def test_reingest_queues_changed_text_only(db: Db, world: dict[str, Any]) -> None:
    app = create_app(local_settings())
    with TestClient(app):
        repo = RecordRepo(app.state.task_context.pool)
        ws = WorkspaceId(world["ws"])

        def upsert(body: str) -> UUID:
            record_id, _ = repo.upsert_record(
                workspace_id=ws,
                record=RecordInput(
                    connection_id=world["conn"],
                    provider="email_upload",
                    external_id="m-1",
                    kind="email",
                    client_id=world["acme"],
                    policy="full",
                    thread_key="t9",
                    occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                    direction="in",
                    word_count=len(body.split()),
                    metadata={},
                    body_clean=body,
                    raw_object_uri=None,
                    deep_link=None,
                    content_hash=hashlib.sha256(body.encode()).digest(),
                ),
                author_person_id=world["jane"],
                participants=[],
            )
            return record_id

        record_id = upsert("First version of the message.")
        assert _state(db, record_id)["state"] == "pending"
        db.execute(
            "update public.source_record set processing_state = 'done' where id = %s", (record_id,)
        )
        upsert("First version of the message.")
        assert _state(db, record_id)["state"] == "done", "unchanged re-sync keeps its state"
        upsert("Edited version of the message.")
        assert _state(db, record_id)["state"] == "pending", "changed text is processed again"


def test_sweep_requests_one_processing_job_per_workspace(db: Db, world: dict[str, Any]) -> None:
    _record(db, world, "late", body=LATE, minutes=0)
    queue = CapturingQueue()
    app = create_app(local_settings())
    headers = {"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"}
    with TestClient(app) as client:
        context = app.state.task_context
        llm = Llm(backend=ScriptedBackend(), models=MODELS, usage=LlmUsageRepo(context.pool))
        app.state.task_context = dataclasses.replace(
            context, llm=llm, queue=queue, processing_enabled=True
        )
        client.post("/maintenance/sweep", headers=headers)
        client.post("/maintenance/sweep", headers=headers)
    mine = [
        e for e in queue.envelopes if e.type == "process.records" and e.workspace_id == world["ws"]
    ]
    assert len(mine) == 1, "a queued processing job blocks a second one"
    assert mine[0].payload == {"trigger": "sweep"}


def test_reprocessing_keeps_evidence_of_unchanged_quotes(db: Db, world: dict[str, Any]) -> None:
    record = _record(db, world, "late", body=LATE, minutes=0)
    _run(db, world, ScriptedBackend())
    row = db.execute(
        "select id from public.evidence where workspace_id = %s and source_record_id = %s",
        (world["ws"], record),
    ).fetchone()
    evidence_id = _id(row)

    with db.transaction():
        commitment = _id(
            db.execute(
                "insert into public.commitment (workspace_id, client_id, owner_side, text, "
                "stated_at, dedupe_key) values (%s, %s, 'firm', 'Deliver the Q3 report', now(), "
                "'q3') returning id",
                (world["ws"], world["acme"]),
            ).fetchone()
        )
        db.execute(
            "insert into public.commitment_evidence (workspace_id, commitment_id, evidence_id, "
            "client_id) values (%s, %s, %s, %s)",
            (world["ws"], commitment, evidence_id, world["acme"]),
        )

    db.execute(
        "update public.source_record set processing_state = 'pending' where id = %s", (record,)
    )
    _run(db, world, ScriptedBackend())
    rows = db.execute(
        "select id from public.evidence where workspace_id = %s and source_record_id = %s",
        (world["ws"], record),
    ).fetchall()
    assert [r["id"] for r in rows] == [evidence_id], "unchanged quote keeps its evidence id"
    links = db.execute(
        "select count(*)::int as n from public.commitment_evidence where commitment_id = %s",
        (commitment,),
    ).fetchone()
    assert links is not None and links["n"] == 1, "derived rows keep their evidence"

    db.execute(
        "update public.source_record set processing_state = 'pending', "
        "body_clean = 'Nothing to report this week.' where id = %s",
        (record,),
    )
    _run(db, world, ScriptedBackend())
    left = db.execute(
        "select count(*)::int as n from public.evidence where workspace_id = %s "
        "and source_record_id = %s",
        (world["ws"], record),
    ).fetchone()
    assert left is not None and left["n"] == 0, "a quote the new run no longer produces is removed"


def test_a_policy_change_during_processing_stores_nothing(db: Db, world: dict[str, Any]) -> None:
    record = _record(db, world, "late", body=LATE, minutes=0)
    backend = ScriptedBackend()
    generate = backend.generate

    def flip_policy_then_answer(*args: Any, **kwargs: Any) -> Generation:
        db.execute(
            "update public.client set policy = 'metadata_only' where id = %s", (world["acme"],)
        )
        return generate(*args, **kwargs)

    backend.generate = flip_policy_then_answer  # type: ignore[method-assign]
    progress = _run(db, world, backend)

    assert progress["changed"] == 1
    for table in ("evidence", "extraction"):
        row = db.execute(
            f"select count(*)::int as n from public.{table} where workspace_id = %s",  # noqa: S608
            (world["ws"],),
        ).fetchone()
        assert row is not None and row["n"] == 0, f"{table} must stay empty"
    # Never done: the next pass sees the new policy and retires the record.
    assert _state(db, record)["state"] == "not_applicable"


@pytest.mark.parametrize("always_fail", [False, True])
def test_local_queue_redelivers_retryable_processing_error(
    db: Db, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, always_fail: bool
) -> None:
    late = _record(db, world, "retry-late", body=LATE, minutes=0)
    promise = _record(
        db,
        world,
        "retry-promise",
        body=PROMISE,
        minutes=1,
        thread="t2",
        author="ana",
        direction="out",
    )
    job = _id(
        db.execute(
            "insert into public.job (workspace_id, type, idempotency_key, payload, max_attempts) "
            "values (%s, 'process.records', %s, '{\"trigger\": \"sweep\"}', 3) returning id",
            (world["ws"], f"local-retry-{uuid4()}"),
        ).fetchone()
    )
    backend = ScriptedBackend()
    generate = backend.generate
    calls = 0

    def fail_mid_processing(*args: Any, **kwargs: Any) -> Generation:
        nonlocal calls
        calls += 1
        if always_fail or calls == 2:
            raise ProviderError(503, "UNAVAILABLE")
        return generate(*args, **kwargs)

    backend.generate = fail_mid_processing  # type: ignore[method-assign]
    monkeypatch.setenv("FIRRA_DATABASE_URL", local_settings().database_url.get_secret_value())
    envelope = TaskEnvelope(
        workspace_id=world["ws"],
        job_id=job,
        type="process.records",
        idempotency_key="local-retry",
        payload={"trigger": "sweep"},
    )
    statuses: list[int] = []
    sleeps: list[float] = []
    app = create_app(local_settings())
    with TestClient(app) as worker:
        context = app.state.task_context
        downstream = CapturingQueue()
        app.state.task_context = dataclasses.replace(
            context,
            llm=Llm(backend=backend, models=MODELS, usage=LlmUsageRepo(context.pool)),
            queue=downstream,
            processing_enabled=True,
        )

        def deliver(request: httpx.Request) -> httpx.Response:
            response = worker.post(
                request.url.path, content=request.content, headers=dict(request.headers)
            )
            statuses.append(response.status_code)
            return httpx.Response(response.status_code, content=response.content)

        with httpx.Client(transport=httpx.MockTransport(deliver)) as client:
            queue = LocalQueue("http://worker", LOCAL_SECRET, client, sleeps.append)
            if always_fail:
                with pytest.raises(LocalTaskFailedError):
                    queue._post(envelope)
            else:
                queue._post(envelope)
    row = db.execute(
        "select status, attempts, error_class from public.job where workspace_id = %s and id = %s",
        (world["ws"], job),
    ).fetchone()
    assert row is not None
    if always_fail:
        assert statuses == [503, 503, 200]
        assert sleeps == [1, 2]
        assert row == {"status": "failed", "attempts": 3, "error_class": "provider"}
        assert downstream.envelopes == []
    else:
        assert statuses == [503, 200]
        assert sleeps == [1]
        assert row == {"status": "succeeded", "attempts": 2, "error_class": None}
        assert calls == 3  # first record is not sent to the model again
        assert _state(db, late)["state"] == _state(db, promise)["state"] == "done"
        assert [e.type for e in downstream.envelopes] == ["memory.rebuild"]


def test_local_queue_exhaustion_without_worker_claim_is_terminal(
    db: Db, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _id(
        db.execute(
            "insert into public.job (workspace_id, type, idempotency_key, payload, max_attempts) "
            "values (%s, 'process.records', %s, '{\"trigger\": \"sweep\"}', 2) returning id",
            (world["ws"], f"unclaimed-{uuid4()}"),
        ).fetchone()
    )
    monkeypatch.setenv("FIRRA_DATABASE_URL", local_settings().database_url.get_secret_value())
    envelope = TaskEnvelope(
        workspace_id=world["ws"],
        job_id=job,
        type="process.records",
        idempotency_key="unclaimed",
        payload={"trigger": "sweep"},
    )
    sleeps: list[float] = []
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client,
        pytest.raises(LocalTaskFailedError),
    ):
        LocalQueue("http://worker", LOCAL_SECRET, client, sleeps.append)._post(envelope)
    row = db.execute(
        "select status, attempts, error_class, finished_at from public.job "
        "where workspace_id = %s and id = %s",
        (world["ws"], job),
    ).fetchone()
    assert row is not None
    assert row["status"] == "failed" and row["attempts"] == 0
    assert row["error_class"] == "local_dispatch_exhausted"
    assert row["finished_at"] is not None
    assert sleeps == [1]


def test_mid_batch_retry_preserves_every_completed_record_counter(
    db: Db, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _record(db, world, "progress-first", body=LATE, minutes=0)
    second = _record(
        db,
        world,
        "progress-second",
        body=PROMISE,
        minutes=1,
        thread="t2",
        author="ana",
        direction="out",
    )
    third = _record(db, world, "progress-third", body=LATE, minutes=2, thread="t3")
    job = _id(
        db.execute(
            "insert into public.job (workspace_id,type,idempotency_key,payload) "
            "values (%s,'process.records',%s,'{\"trigger\":\"sweep\"}') returning id",
            (world["ws"], f"progress-{uuid4()}"),
        ).fetchone()
    )
    backend = ScriptedBackend()
    generate = backend.generate
    calls = 0
    progress_updates = 0
    execute = psycopg.Connection.execute

    def count_update(self: Any, query: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal progress_updates
        if isinstance(query, str) and "update public.job set progress" in query:
            progress_updates += 1
        return execute(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", count_update)

    def fail_third_once(*args: Any, **kwargs: Any) -> Generation:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ProviderError(503, "UNAVAILABLE")
        return generate(*args, **kwargs)

    backend.generate = fail_third_once  # type: ignore[method-assign]
    app = create_app(local_settings())
    envelope = {
        "workspace_id": str(world["ws"]),
        "job_id": str(job),
        "idempotency_key": "progress",
        "type": "process.records",
        "payload": {"trigger": "sweep"},
    }
    headers = {"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"}
    with TestClient(app) as client:
        ctx = app.state.task_context
        app.state.task_context = dataclasses.replace(
            ctx,
            llm=Llm(backend=backend, models=MODELS, usage=LlmUsageRepo(ctx.pool)),
            queue=CapturingQueue(),
            processing_enabled=True,
        )
        assert (
            client.post("/tasks/process.records", headers=headers, json=envelope).status_code == 503
        )
        checkpoint = db.execute(
            "select status,progress from public.job where workspace_id=%s and id=%s",
            (world["ws"], job),
        ).fetchone()
        assert checkpoint is not None and checkpoint["status"] == "queued"
        assert checkpoint["progress"]["extracted"] == 2 and checkpoint["progress"]["items"] == 3
        assert checkpoint["progress"]["dropped"] == {"quote_not_found": 1, "tone_not_client": 1}
        assert _state(db, first)["state"] == _state(db, second)["state"] == "done"
        assert _state(db, third)["state"] == "pending"
        assert client.post("/tasks/process.records", headers=headers, json=envelope).json() == {
            "status": "succeeded"
        }
        inputs = EvalRepo(ctx.pool, WorkspaceId(world["ws"])).collect()
    row = db.execute(
        "select status,attempts,progress from public.job where workspace_id=%s and id=%s",
        (world["ws"], job),
    ).fetchone()
    assert row is not None and row["status"] == "succeeded" and row["attempts"] == 2
    assert row["progress"]["extracted"] == 3 and row["progress"]["items"] == 5
    assert row["progress"]["dropped"] == {"quote_not_found": 2, "tone_not_client": 1}
    assert len(inputs.items) == 5 and inputs.verify_dropped == 2 and inputs.items_attempted == 8
    assert calls == 4 and len(backend.calls) == 3 and progress_updates == 3
    assert _state(db, third)["state"] == "done"


def test_record_completion_rolls_back_if_progress_owner_is_stale(
    db: Db, world: dict[str, Any]
) -> None:
    record = _record(db, world, "atomic-progress", body=LATE, minutes=0)
    app = create_app(local_settings())
    with TestClient(app):
        ctx = app.state.task_context
        job = ctx.jobs.create(
            workspace_id=WorkspaceId(world["ws"]),
            job_type="process.records",
            idempotency_key="atomic-progress",
            payload={"trigger": "sweep"},
        )
        assert job is not None
        claimed = ctx.jobs.claim(
            workspace_id=WorkspaceId(world["ws"]), job_id=job, job_type="process.records"
        )
        assert claimed is not None
        stale = dataclasses.replace(claimed, attempts=0)
        repo = ProcessingRepo(ctx.pool)
        with (
            pytest.raises(RuntimeError, match="ownership lost"),
            repo.record_progress(job=stale, progress=lambda: {"extracted": 1}),
        ):
            repo.mark(workspace_id=WorkspaceId(world["ws"]), record_id=record, state="done")
        assert _state(db, record)["state"] == "pending"
        row = db.execute(
            "select progress from public.job where workspace_id=%s and id=%s", (world["ws"], job)
        ).fetchone()
        assert row is not None and row["progress"] == {}
