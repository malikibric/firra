"""needs.update on local Postgres with scripted embeddings; no Vertex calls."""

import dataclasses
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from firra.app import create_app
from firra.db.llm_usage import LlmUsageRepo
from firra.db.memory import MemoryRepo
from firra.db.needs import NeedsRepo
from firra.db.types import WorkspaceId
from firra.llm.models import ModelSet
from firra.llm.service import BudgetExceededError, Llm
from firra.llm.vertex import Embedding, Usage
from firra.memory.rules import load_config as load_memory_config
from tests.conftest import LOCAL_SECRET, local_settings
from tests.integration.test_memory_rebuild import _extraction
from tests.tokens import local_token

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]
MODELS = ModelSet("eu", None, None, "gemini-embedding-001", None)


@dataclasses.dataclass
class ScriptedBackend:
    calls: list[tuple[str, str, int]] = dataclasses.field(default_factory=list)
    fail_at: int | None = None

    def embed(self, model: str, text: str, *, task_type: str, dimensionality: int) -> Embedding:
        assert model == MODELS.embed
        self.calls.append((text, task_type, dimensionality))
        if self.fail_at == len(self.calls):
            raise BudgetExceededError("scripted budget limit")
        first = 1.0 if "attribution" in text.lower() else 0.0
        values = [first, 1.0 - first] + [0.0] * (dimensionality - 2)
        return Embedding(values=values, usage=Usage(input_tokens=3))

    def generate(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("needs.update does not generate text")


@pytest.fixture
def db() -> Iterator[Db]:
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as conn:
        yield conn


def _id(row: dict[str, Any] | None) -> UUID:
    assert row is not None
    return UUID(str(row["id"]))


def _seed_request(
    db: Db,
    workspace: UUID,
    client: UUID,
    connection: UUID,
    *,
    key: str,
    text: str,
    when: datetime,
    thread: str,
) -> UUID:
    with db.transaction():
        record = _id(
            db.execute(
                "insert into public.source_record (workspace_id, connection_id, provider, "
                "external_id, kind, client_id, policy_at_ingest, thread_key, "
                "occurred_at, direction) "
                "values (%s, %s, 'email_upload', %s, 'email', %s, 'full', %s, %s, 'in') "
                "returning id",
                (workspace, connection, key, client, thread, when),
            ).fetchone()
        )
        evidence = _id(
            db.execute(
                "insert into public.evidence "
                "(workspace_id, source_record_id, client_id, quote, locator) "
                "values (%s, %s, %s, 'Please help with attribution', '{\"start\":0,\"end\":28}') "
                "returning id",
                (workspace, record, client),
            ).fetchone()
        )
        request = _id(
            db.execute(
                "insert into public.request (workspace_id, client_id, origin_kind, text, "
                "source_record_id, thread_key, occurred_at, dedupe_key) "
                "values (%s, %s, 'request', %s, %s, %s, %s, %s) returning id",
                (workspace, client, text, record, thread, when, key),
            ).fetchone()
        )
        db.execute(
            "insert into public.request_evidence "
            "(workspace_id, client_id, request_id, evidence_id) "
            "values (%s, %s, %s, %s)",
            (workspace, client, request, evidence),
        )
    return request


@pytest.fixture
def world(db: Db) -> Iterator[dict[str, Any]]:
    workspace = _id(
        db.execute(
            "insert into public.workspace (name) values ('needs-test') returning id"
        ).fetchone()
    )
    other = _id(
        db.execute(
            "insert into public.workspace (name) values ('needs-other') returning id"
        ).fetchone()
    )
    ids: dict[str, Any] = {"workspace": workspace, "other": other}
    for name, owner, policy in [
        ("halden", workspace, "full"),
        ("quillon", workspace, "full"),
        ("metadata", workspace, "full"),
        ("foreign", other, "full"),
    ]:
        ids[name] = _id(
            db.execute(
                "insert into public.client (workspace_id, name, policy) values (%s, %s, %s) "
                "returning id",
                (owner, name, policy),
            ).fetchone()
        )
    for name, owner in [("connection", workspace), ("other_connection", other)]:
        ids[name] = _id(
            db.execute(
                "insert into public.connection (workspace_id, provider, mode, account_label) "
                "values (%s, 'email_upload', 'upload', 'needs') returning id",
                (owner,),
            ).fetchone()
        )
    ids["service"] = _id(
        db.execute(
            "insert into public.workspace_service (workspace_id, name, description) "
            "values (%s, 'Attribution', 'Measurement service') returning id",
            (workspace,),
        ).fetchone()
    )
    for key, day, thread in [
        ("h1", "2026-07-01", "one"),
        ("h2", "2026-07-15", "two"),
        ("h3", "2026-08-05", "two"),
        ("h4", "2026-08-19", "three"),
    ]:
        _seed_request(
            db,
            workspace,
            ids["halden"],
            ids["connection"],
            key=key,
            text=f"attribution request {key}",
            when=datetime.fromisoformat(day).replace(tzinfo=UTC),
            thread=thread,
        )
    for key in ("q1", "q2"):
        _seed_request(
            db,
            workspace,
            ids["quillon"],
            ids["connection"],
            key=key,
            text=f"other request {key}",
            when=datetime(2026, 8, 1, tzinfo=UTC),
            thread="same",
        )
    _seed_request(
        db,
        workspace,
        ids["metadata"],
        ids["connection"],
        key="m1",
        text="attribution metadata-only client",
        when=datetime(2026, 8, 1, tzinfo=UTC),
        thread="meta",
    )
    # Simulate a stale derived row after a policy change; the read path must still filter it.
    db.execute(
        "update public.client set policy = 'metadata_only' where id = %s", (ids["metadata"],)
    )
    _seed_request(
        db,
        other,
        ids["foreign"],
        ids["other_connection"],
        key="f1",
        text="attribution other workspace",
        when=datetime(2026, 8, 1, tzinfo=UTC),
        thread="foreign",
    )
    yield ids
    db.execute("delete from public.workspace where id = %s", (workspace,))
    db.execute("delete from public.workspace where id = %s", (other,))


def _run(
    db: Db,
    world: dict[str, Any],
    backend: ScriptedBackend,
    *,
    enabled: bool,
    expected: str = "succeeded",
) -> dict[str, Any]:
    payload = {"trigger": "manual", "as_of": "2026-09-30"}
    job = _id(
        db.execute(
            "insert into public.job (workspace_id, type, idempotency_key, payload) "
            "values (%s, 'needs.update', %s, %s) returning id",
            (world["workspace"], str(uuid4()), psycopg.types.json.Jsonb(payload)),
        ).fetchone()
    )
    app = create_app(local_settings())
    with TestClient(app) as client:
        context = app.state.task_context
        llm = Llm(backend=backend, models=MODELS, usage=LlmUsageRepo(context.pool))
        app.state.task_context = dataclasses.replace(context, llm=llm, processing_enabled=enabled)
        response = client.post(
            "/tasks/needs.update",
            headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
            json={
                "workspace_id": str(world["workspace"]),
                "job_id": str(job),
                "idempotency_key": "test",
                "type": "needs.update",
                "payload": payload,
            },
        )
    assert response.status_code == 200, response.text
    row = db.execute(
        "select status::text as status, progress, error_class from public.job where id = %s",
        (job,),
    ).fetchone()
    assert row is not None and row["status"] == expected, row
    return {**cast(dict[str, Any], row["progress"]), "error_class": row["error_class"]}


def test_exact_counts_service_match_and_policy_isolation(db: Db, world: dict[str, Any]) -> None:
    backend = ScriptedBackend()
    progress = _run(db, world, backend, enabled=True)
    assert progress["requests_embedded"] == 6
    assert progress["needs_written"] == 1
    assert len(backend.calls[:-1]) == 6
    assert backend.calls[-1][0] == "Attribution. Measurement service"
    assert all(task == "CLUSTERING" and dimensions == 768 for _, task, dimensions in backend.calls)
    need = db.execute(
        "select id, client_id, topic, workspace_service_id, mention_count, thread_count, "
        "first_seen_at, last_seen_at from public.latent_need where workspace_id = %s",
        (world["workspace"],),
    ).fetchone()
    assert need is not None
    assert (need["client_id"], need["topic"], need["workspace_service_id"]) == (
        world["halden"],
        None,
        world["service"],
    )
    assert (need["mention_count"], need["thread_count"]) == (4, 3)
    assert need["first_seen_at"].date().isoformat() == "2026-07-01"
    assert need["last_seen_at"].date().isoformat() == "2026-08-19"
    request_links = db.execute(
        "select count(*)::int as n from public.latent_need_request "
        "where workspace_id = %s and latent_need_id = %s",
        (world["workspace"], need["id"]),
    ).fetchone()
    evidence_links = db.execute(
        "select count(*)::int as n from public.latent_need_evidence "
        "where workspace_id = %s and latent_need_id = %s",
        (world["workspace"], need["id"]),
    ).fetchone()
    assert request_links is not None and request_links["n"] == 4
    assert evidence_links is not None and evidence_links["n"] == 4
    metadata_count = db.execute(
        "select count(*)::int as n from public.request where workspace_id = %s "
        "and client_id = %s and embedding is not null",
        (world["workspace"], world["metadata"]),
    ).fetchone()
    other_count = db.execute(
        "select count(*)::int as n from public.request where workspace_id = %s "
        "and embedding is not null",
        (world["other"],),
    ).fetchone()
    assert metadata_count is not None and metadata_count["n"] == 0
    assert other_count is not None and other_count["n"] == 0
    assert not backend.calls or all("metadata-only" not in text for text, _, _ in backend.calls)

    again = ScriptedBackend()
    _run(db, world, again, enabled=True)
    assert len(again.calls) == 1  # service embedded once; requests are cached in rows
    disabled = ScriptedBackend()
    assert _run(db, world, disabled, enabled=False)["status"] == "disabled"
    assert disabled.calls == []


def test_budget_failure_writes_no_embeddings_or_needs(db: Db, world: dict[str, Any]) -> None:
    backend = ScriptedBackend(fail_at=3)
    progress = _run(db, world, backend, enabled=True, expected="failed")
    assert progress["error_class"] == "budget_exceeded"
    embedded = db.execute(
        "select count(*)::int as n from public.request where workspace_id = %s "
        "and embedding is not null",
        (world["workspace"],),
    ).fetchone()
    needs = db.execute(
        "select count(*)::int as n from public.latent_need where workspace_id = %s",
        (world["workspace"],),
    ).fetchone()
    assert embedded is not None and embedded["n"] == 0
    assert needs is not None and needs["n"] == 0


def test_stale_open_need_is_removed_and_addressed_need_is_preserved(
    db: Db, world: dict[str, Any]
) -> None:
    _run(db, world, ScriptedBackend(), enabled=True)
    original = db.execute(
        "select id from public.latent_need where workspace_id = %s and client_id = %s",
        (world["workspace"], world["halden"]),
    ).fetchone()
    assert original is not None
    db.execute(
        "delete from public.request where workspace_id = %s and client_id = %s "
        "and dedupe_key in ('h1', 'h2')",
        (world["workspace"], world["halden"]),
    )
    _run(db, world, ScriptedBackend(), enabled=True)
    assert (
        db.execute(
            "select id from public.latent_need where workspace_id = %s and client_id = %s",
            (world["workspace"], world["halden"]),
        ).fetchone()
        is None
    )

    for key, day in (("h5", 1), ("h6", 2), ("h7", 3)):
        _seed_request(
            db,
            world["workspace"],
            world["halden"],
            world["connection"],
            key=key,
            text=f"attribution request {key}",
            when=datetime(2026, 8, day, tzinfo=UTC),
            thread=key,
        )
    _run(db, world, ScriptedBackend(), enabled=True)
    db.execute(
        "update public.latent_need set status = 'addressed' "
        "where workspace_id = %s and client_id = %s",
        (world["workspace"], world["halden"]),
    )
    _run(db, world, ScriptedBackend(), enabled=True)
    addressed = db.execute(
        "select status::text as status from public.latent_need "
        "where workspace_id = %s and client_id = %s",
        (world["workspace"], world["halden"]),
    ).fetchone()
    assert addressed is not None and addressed["status"] == "addressed"


def test_only_one_active_needs_job_per_workspace(db: Db, world: dict[str, Any]) -> None:
    app = create_app(local_settings())
    with TestClient(app):
        repo = NeedsRepo(app.state.task_context.pool)
        workspace_id = WorkspaceId(world["workspace"])
        payload: dict[str, object] = {"trigger": "memory", "as_of": None}
        first = repo.request_job(workspace_id=workspace_id, key="needs:first", payload=payload)
        assert first is not None
        assert (
            repo.request_job(workspace_id=workspace_id, key="needs:second", payload=payload) is None
        )
        db.execute(
            "update public.job set status = 'succeeded' where workspace_id = %s and id = %s",
            (world["workspace"], first),
        )
        assert repo.request_job(workspace_id=workspace_id, key="needs:second", payload=payload)


def test_no_qualifying_cluster_skips_service_embeddings(db: Db, world: dict[str, Any]) -> None:
    db.execute(
        "delete from public.request where workspace_id = %s and client_id = %s",
        (world["workspace"], world["halden"]),
    )
    backend = ScriptedBackend()
    progress = _run(db, world, backend, enabled=True)
    assert progress["needs_written"] == 0
    assert sorted(text for text, _, _ in backend.calls) == ["other request q1", "other request q2"]
    again = ScriptedBackend()
    _run(db, world, again, enabled=True)
    assert again.calls == []


def test_memory_text_change_invalidates_embedding_and_needs_reembeds(
    db: Db, world: dict[str, Any]
) -> None:
    workspace_id = WorkspaceId(world["workspace"])
    client_id = _id(
        db.execute(
            "insert into public.client (workspace_id, name, policy) "
            "values (%s, 'changed-request', 'full') returning id",
            (workspace_id,),
        ).fetchone()
    )
    record_id = _extraction(
        db,
        workspace_id,
        world["connection"],
        client_id,
        "changed-request",
        "request",
        "Client asks for attribution.",
        "Please help with attribution reporting.",
        datetime(2026, 8, 1, tzinfo=UTC),
    )
    app = create_app(local_settings())
    with TestClient(app):
        repo = MemoryRepo(app.state.task_context.pool)

        def rebuild() -> None:
            repo.rebuild_client(
                workspace_id=workspace_id,
                client_id=client_id,
                as_of=date(2026, 9, 30),
                cfg=load_memory_config(),
            )

        rebuild()
        _run(db, world, ScriptedBackend(), enabled=True)
        original = db.execute(
            "select id, embedding, embedding_model from public.request "
            "where workspace_id = %s and client_id = %s",
            (workspace_id, client_id),
        ).fetchone()
        assert original is not None and original["embedding"] is not None
        assert original["embedding_model"] == MODELS.embed
        rebuild()
        unchanged = db.execute(
            "select embedding, embedding_model from public.request "
            "where workspace_id = %s and id = %s",
            (workspace_id, original["id"]),
        ).fetchone()
        assert unchanged is not None and unchanged["embedding"] == original["embedding"]
        assert unchanged["embedding_model"] == original["embedding_model"]
        updated_text = "Client requests attribution measurement reporting."
        db.execute(
            "update public.extraction set payload = "
            "jsonb_set(payload, '{text}', to_jsonb(%s::text)) "
            "where workspace_id = %s and source_record_id = %s",
            (updated_text, workspace_id, record_id),
        )
        rebuild()
        changed = db.execute(
            "select id, text, embedding, embedding_model from public.request "
            "where workspace_id = %s and client_id = %s",
            (workspace_id, client_id),
        ).fetchone()
        assert changed is not None and changed["id"] == original["id"]
        assert changed["text"] == updated_text
        assert changed["embedding"] is None and changed["embedding_model"] is None
    backend = ScriptedBackend()
    progress = _run(db, world, backend, enabled=True)
    assert progress["requests_embedded"] == 1
    assert [text for text, _, _ in backend.calls] == [
        updated_text,
        "Attribution. Measurement service",
    ]
    reembedded = db.execute(
        "select embedding, embedding_model from public.request where workspace_id = %s and id = %s",
        (workspace_id, original["id"]),
    ).fetchone()
    assert reembedded is not None and reembedded["embedding"] is not None
    assert reembedded["embedding_model"] == MODELS.embed
