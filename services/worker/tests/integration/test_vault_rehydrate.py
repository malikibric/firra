"""Owner-only vault rehydration: fake providers, real jobs and local Postgres."""

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from firra.app import create_app
from firra.clean import clean_email
from firra.connectors.google.auth import GoogleAuth
from firra.connectors.google.credentials import GoogleUserTokens
from firra.connectors.microsoft.auth import MicrosoftAuth
from firra.connectors.microsoft.credentials import MicrosoftUserTokens
from firra.db.retention import purge_expired_bodies
from firra.db.types import WorkspaceId
from firra.tasks.context import TaskContext
from tests.conftest import LOCAL_SECRET, local_settings
from tests.fake_google import FakeGoogle, stored_tokens
from tests.fake_microsoft import FakeMicrosoft, graph_message
from tests.integration.test_gmail_sync import CapturingQueue
from tests.integration.test_vault_retention import (
    DbPool,
    FakeRawStore,
    record,
    upsert,
)
from tests.integration.test_vault_retention import (
    world as retention_world,
)
from tests.mail import email_bytes
from tests.tokens import local_token

pytestmark = pytest.mark.integration
world = retention_world
BODY = "Hello, here is the update."


@dataclass
class Harness:
    app: FastAPI
    pool: DbPool
    items: list[dict[str, UUID]]
    context: TaskContext
    client: TestClient
    fake: FakeGoogle | FakeMicrosoft
    provider: str
    queue: CapturingQueue

    def seed(self, external_id: str = "one", *, body: str = BODY) -> UUID:
        item = self.items[0]
        if isinstance(self.fake, FakeGoogle):
            self.fake.add(
                external_id,
                email_bytes(sender="ceo@acme.com", to=["ana@northbeam.agency"], body=body),
            )
        else:
            self.fake.messages[external_id] = graph_message(
                external_id,
                "2026-09-01T10:00:00Z",
                sender="ceo@acme.com",
                to=["ana@northbeam.agency"],
                body=body,
            )
        cleaned = clean_email(body).body
        rid = upsert(
            self.pool,
            item,
            replace(
                record(item, external_id),
                provider=self.provider,
                body_clean=cleaned,
                content_hash=hashlib.sha256(cleaned.encode()).digest(),
                raw_object_uri=None,
            ),
        )
        with self.pool.connection() as db:
            db.execute(
                "update public.source_record set body_clean = null, body_purged_at = now(), "
                "processing_state = 'done', processed_at = now(), processing_version = 4, "
                "vault_delivered_at = now() where id = %s",
                (rid,),
            )
        return rid

    def run(
        self, *, job: UUID | None = None, user: UUID | None = None, expected_status: int = 200
    ) -> tuple[str, UUID, dict[str, Any]]:
        item = self.items[0]
        payload = {"connection_id": str(item["connection"]), "user_id": str(user or item["user"])}
        if job is None:
            created = self.context.jobs.create(
                workspace_id=WorkspaceId(item["workspace"]),
                job_type="vault.rehydrate",
                idempotency_key=str(uuid4()),
                payload=payload,
                connection_id=item["connection"],
            )
            assert created is not None
            job = created
        response = self.client.post(
            "/tasks/vault.rehydrate",
            headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
            json={
                "workspace_id": str(item["workspace"]),
                "job_id": str(job),
                "idempotency_key": str(uuid4()),
                "type": "vault.rehydrate",
                "payload": payload,
            },
        )
        assert response.status_code == expected_status, response.text
        with self.pool.connection() as db:
            result = db.execute(
                "select progress, error_class from public.job where id = %s", (job,)
            ).fetchone()
        assert result is not None
        return (
            response.json()["status"],
            job,
            {**result["progress"], "error": result["error_class"]},
        )

    def row(self, rid: UUID) -> dict[str, Any]:
        with self.pool.connection() as db:
            result = db.execute(
                "select * from public.source_record where id = %s", (rid,)
            ).fetchone()
        assert result is not None
        return result


@pytest.fixture(params=["gmail", "outlook"])
def harness(
    world: tuple[DbPool, list[dict[str, UUID]]], request: pytest.FixtureRequest
) -> Iterator[Harness]:
    pool, items = world
    provider = str(request.param)
    fake = FakeGoogle() if provider == "gmail" else FakeMicrosoft()
    app = create_app(local_settings())
    context: TaskContext = app.state.task_context
    http, queue = fake.client(), CapturingQueue()
    context = replace(
        context,
        http=http,
        queue=queue,
        raw=FakeRawStore(),
        google=GoogleAuth(GoogleUserTokens("cid", "secret", http), None, context.keyring),
        microsoft=MicrosoftAuth(
            MicrosoftUserTokens("cid", "secret", http), None, context.keyring, context.connections
        ),
    )
    app.state.task_context = context
    with TestClient(app) as client:
        token = (
            stored_tokens("good")
            if provider == "gmail"
            else '{"tenant_id":"tenant-1","refresh_token":"good"}'
        )
        blob = context.keyring.encrypt_token(
            WorkspaceId(items[0]["workspace"]), items[0]["connection"], token
        )
        with pool.connection() as db:
            db.execute(
                "update public.connection set provider = %s, token_ciphertext = %s where id = %s",
                (provider, blob, items[0]["connection"]),
            )
        yield Harness(app, pool, items, context, client, fake, provider, queue)
    http.close()


def test_restores_without_extraction_and_retry_is_noop(harness: Harness) -> None:
    rid = harness.seed(body="Hello, here is the update.\n\nOn Monday someone wrote:\n> Old reply")
    before = harness.row(rid)
    status, job, progress = harness.run()
    assert status == "succeeded" and progress["done"] == progress["total"] == 1
    after = harness.row(rid)
    assert (
        after["body_clean"]
        == clean_email("Hello, here is the update.\n\nOn Monday someone wrote:\n> Old reply").body
    )
    assert after["rehydrated_at"] is not None and after["vault_delivered_at"] is None
    for key in (
        "body_purged_at",
        "processing_state",
        "processed_at",
        "processing_version",
        "content_hash",
    ):
        assert after[key] == before[key]
    calls = len(harness.fake.calls)
    assert harness.run(job=job)[0] == "noop"
    assert len(harness.fake.calls) == calls
    assert harness.run()[2]["total"] == 0
    assert not harness.queue.envelopes
    assert isinstance(harness.context.raw, FakeRawStore) and not harness.context.raw.objects
    with harness.pool.connection() as db:
        assert db.execute(
            "select count(*) as n from public.extraction where workspace_id = %s",
            (harness.items[0]["workspace"],),
        ).fetchone() == {"n": 0}


def test_records_from_before_vault_delivery_get_their_owner_as_recipient(harness: Harness) -> None:
    rid = harness.seed()
    with harness.pool.connection() as db:
        db.execute("update public.source_record set vault_user_id = null where id = %s", (rid,))
    status, _, progress = harness.run()
    assert status == "succeeded" and progress["done"] == progress["total"] == 1
    after = harness.row(rid)
    assert after["body_clean"] is not None
    assert after["vault_user_id"] == harness.items[0]["user"]


def test_another_users_records_are_never_taken_over(harness: Harness) -> None:
    rid = harness.seed()
    other = uuid4()
    with harness.pool.connection() as db:
        db.execute("insert into auth.users (id) values (%s)", (other,))
        db.execute("update public.source_record set vault_user_id = %s where id = %s", (other, rid))
    status, _, progress = harness.run()
    assert status == "succeeded" and progress["total"] == 0
    after = harness.row(rid)
    assert after["body_clean"] is None and after["vault_user_id"] == other


@pytest.mark.parametrize("outcome", ["changed", "missing"])
def test_changed_and_missing_are_skipped(harness: Harness, outcome: str) -> None:
    rid = harness.seed()
    if outcome == "changed":
        with harness.pool.connection() as db:
            db.execute(
                "update public.source_record set content_hash = %s where id = %s",
                (b"different", rid),
            )
    elif isinstance(harness.fake, FakeGoogle):
        harness.fake.messages.pop("one")
    else:
        harness.fake.messages.pop("one")
    status, _, progress = harness.run()
    assert status == "succeeded" and progress[outcome] == 1 and progress["done"] == 0
    assert harness.row(rid)["body_clean"] is None


@pytest.mark.parametrize(
    "exclusion",
    [
        "foreign_user",
        "foreign_workspace",
        "metadata",
        "ingest_metadata",
        "old",
        "calendar",
        "not_purged",
    ],
)
def test_ineligible_rows_are_never_fetched(harness: Harness, exclusion: str) -> None:
    rid = harness.seed()
    with harness.pool.connection() as db:
        if exclusion == "foreign_user":
            db.execute(
                "update public.source_record set vault_user_id = %s where id = %s",
                (harness.items[1]["user"], rid),
            )
        elif exclusion == "foreign_workspace":
            rid = upsert(harness.pool, harness.items[1], record(harness.items[1]))
            db.execute(
                "update public.source_record set body_clean = null, body_purged_at = now() "
                "where id = %s",
                (rid,),
            )
            db.execute(
                "update public.source_record set body_purged_at = null where workspace_id = %s",
                (harness.items[0]["workspace"],),
            )
        elif exclusion == "metadata":
            db.execute(
                "update public.client set policy = 'metadata_only' where id = %s",
                (harness.items[0]["client"],),
            )
        elif exclusion == "ingest_metadata":
            db.execute(
                "update public.source_record set policy_at_ingest = 'metadata_only' where id = %s",
                (rid,),
            )
        elif exclusion == "old":
            db.execute(
                "update public.source_record set occurred_at = now() - interval '2 years' "
                "where id = %s",
                (rid,),
            )
        elif exclusion == "calendar":
            db.execute(
                "update public.source_record set kind = 'calendar_event' where id = %s", (rid,)
            )
        else:
            db.execute(
                "update public.source_record set body_purged_at = null where id = %s", (rid,)
            )
    assert harness.run()[2]["done"] == 0
    assert not harness.fake.calls
    if exclusion != "foreign_workspace":
        assert harness.row(rid)["body_clean"] is None
    else:
        assert harness.row(rid)["body_clean"] is None


def test_foreign_caller_gets_success_zero(harness: Harness) -> None:
    rid = harness.seed()
    status, _, progress = harness.run(user=harness.items[1]["user"])
    assert status == "succeeded" and progress["total"] == progress["done"] == 0
    assert harness.row(rid)["body_clean"] is None and not harness.fake.calls


def test_policy_change_during_fetch_prevents_restore(harness: Harness) -> None:
    rid = harness.seed()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/messages/one" in request.url.path:
            with harness.pool.connection() as db:
                db.execute(
                    "update public.client set policy = 'metadata_only' where id = %s",
                    (harness.items[0]["client"],),
                )
        return harness.fake.handler(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        harness.app.state.task_context = replace(harness.context, http=http)
        status, _, progress = harness.run()
    assert status == "succeeded" and progress["failed"] == 1 and progress["done"] == 0
    assert harness.row(rid)["body_clean"] is None


def test_revoked_credentials_fail_with_reconnect_class(harness: Harness) -> None:
    rid = harness.seed()
    harness.fake.revoked_refresh_tokens.add("good")
    status, _, progress = harness.run()
    assert status == "failed" and progress["error"] == "token_revoked" and progress["failed"] == 1
    assert harness.row(rid)["body_clean"] is None
    with harness.pool.connection() as db:
        result = db.execute(
            "select status, last_error_class from public.connection where id = %s",
            (harness.items[0]["connection"],),
        ).fetchone()
    assert result == {"status": "revoked", "last_error_class": "token_revoked"}


def test_continuations_resume_without_double_counting(harness: Harness) -> None:
    ids = [harness.seed(str(i)) for i in range(3)]
    harness.app.state.task_context = replace(harness.context, time_budget_seconds=0)
    status, job, progress = harness.run()
    rounds = 0
    while status == "continued":
        rounds += 1
        status, job, progress = harness.run(job=job)
    assert rounds == 3 and status == "succeeded"
    assert progress["total"] == progress["done"] == 3
    assert progress["failed"] == progress["missing"] == progress["changed"] == 0
    assert all(harness.row(rid)["body_clean"] == BODY for rid in ids)
    assert len([call for call in harness.fake.calls if "/messages/" in call]) == 3


def test_keyset_batches_with_equal_timestamps(harness: Harness) -> None:
    ids = [harness.seed(str(i)) for i in range(51)]
    with harness.pool.connection() as db:
        db.execute(
            "update public.source_record set occurred_at = %s where workspace_id = %s",
            (datetime.now(UTC), harness.items[0]["workspace"]),
        )
    status, _, progress = harness.run()
    assert status == "succeeded" and progress["done"] == progress["total"] == 51
    assert all(harness.row(rid)["body_clean"] == BODY for rid in ids)


@pytest.mark.parametrize(
    ("state", "hours", "delivered", "recipient", "purged"),
    [
        ("done", 1, False, True, False),
        ("done", 71, False, True, False),
        ("done", 73, False, True, True),
        ("pending", 73, False, True, False),
        ("pending", 169, False, True, True),
        ("error", 167, False, True, False),
        ("error", 169, False, True, True),
        ("done", 1, True, True, True),
        ("done", 1, False, False, True),
        ("not_applicable", 1, True, True, True),
    ],
)
def test_retention_starts_from_rehydration(
    world: tuple[DbPool, list[dict[str, UUID]]],
    state: str,
    hours: int,
    delivered: bool,
    recipient: bool,
    purged: bool,
) -> None:
    pool, items = world
    rid = upsert(pool, items[0], replace(record(items[0]), raw_object_uri=None))
    with pool.connection() as db:
        db.execute(
            "update public.source_record set created_at = now() - interval '1 year', "
            "rehydrated_at = now() - make_interval(hours => %s), processing_state = %s, "
            "vault_delivered_at = %s, vault_user_id = %s where id = %s",
            (
                hours,
                state,
                datetime.now(UTC) if delivered else None,
                items[0]["user"] if recipient else None,
                rid,
            ),
        )
        db.execute("alter table public.source_record disable trigger source_record_set_updated_at")
        db.execute(
            "update public.source_record set updated_at = now() - interval '1 hour' where id = %s",
            (rid,),
        )
        db.execute("alter table public.source_record enable trigger source_record_set_updated_at")
    assert purge_expired_bodies(pool, FakeRawStore()).purged == int(purged)


def test_unchanged_resync_preserves_rehydration(harness: Harness) -> None:
    rid = harness.seed()
    assert harness.run()[2]["done"] == 1
    before = harness.row(rid)
    item = harness.items[0]
    upsert(
        harness.pool,
        item,
        replace(
            record(item),
            provider=harness.provider,
            body_clean=BODY,
            content_hash=hashlib.sha256(BODY.encode()).digest(),
            raw_object_uri=None,
        ),
    )
    after = harness.row(rid)
    assert after["body_clean"] == BODY
    for key in (
        "rehydrated_at",
        "body_purged_at",
        "processing_state",
        "processed_at",
        "processing_version",
    ):
        assert after[key] == before[key]


@pytest.mark.parametrize("status", ["active", "revoked"])
def test_contributor_ownership(harness: Harness, status: str) -> None:
    rid = harness.seed()
    item = harness.items[0]
    with harness.pool.connection() as db:
        if (
            db.execute(
                "select 1 from information_schema.columns where table_schema = 'public' "
                "and table_name = 'contributor' and column_name = 'user_id'"
            ).fetchone()
            is None
        ):
            pytest.skip("T61 contributor accounts are not installed")
        row = db.execute(
            "insert into public.contributor (workspace_id, email, status, user_id) "
            "values (%s, 'synthetic@example.test', %s, %s) returning id",
            (item["workspace"], status, item["user"]),
        ).fetchone()
        assert row is not None
        db.execute(
            "update public.connection set owner_user_id = null, owner_contributor_id = %s "
            "where id = %s",
            (row["id"], item["connection"]),
        )
    result, _, progress = harness.run()
    assert result == "succeeded" and progress["done"] == int(status == "active")
    assert (harness.row(rid)["body_clean"] is not None) == (status == "active")


def test_existing_extractions_and_evidence_stay_unchanged(harness: Harness) -> None:
    rid = harness.seed()
    item = harness.items[0]
    with harness.pool.connection() as db:
        ev = db.execute(
            "insert into public.evidence (workspace_id, source_record_id, client_id, "
            "quote, locator) "
            'values (%s, %s, %s, %s, \'{"start":0,"end":25}\') returning id',
            (item["workspace"], rid, item["client"], BODY),
        ).fetchone()
        ex = db.execute(
            "insert into public.extraction (workspace_id, source_record_id, client_id, kind, "
            "payload, model, prompt_version, processing_version, item_index) values "
            "(%s, %s, %s, 'commitment', '{\"text\":\"synthetic\"}', 'fake', 'extract.v1', 4, 0) "
            "returning id",
            (item["workspace"], rid, item["client"]),
        ).fetchone()
        assert ev is not None and ex is not None
        db.execute(
            "insert into public.extraction_evidence (workspace_id, extraction_id, "
            "evidence_id, client_id) values (%s, %s, %s, %s)",
            (item["workspace"], ex["id"], ev["id"], item["client"]),
        )
        before_ev = db.execute(
            "select * from public.evidence where id = %s", (ev["id"],)
        ).fetchone()
        before_ex = db.execute(
            "select * from public.extraction where id = %s", (ex["id"],)
        ).fetchone()
    assert harness.run()[2]["done"] == 1
    with harness.pool.connection() as db:
        assert (
            db.execute("select * from public.evidence where id = %s", (ev["id"],)).fetchone()
            == before_ev
        )
        assert (
            db.execute("select * from public.extraction where id = %s", (ex["id"],)).fetchone()
            == before_ex
        )


def test_body_and_cursor_roll_back_when_job_checkpoint_fails(harness: Harness) -> None:
    rid = harness.seed()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/messages/one" in request.url.path:
            with harness.pool.connection() as db:
                db.execute(
                    "update public.job set status = 'queued' where workspace_id = %s "
                    "and type = 'vault.rehydrate'",
                    (harness.items[0]["workspace"],),
                )
        return harness.fake.handler(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        harness.app.state.task_context = replace(harness.context, http=http)
        status, job, progress = harness.run(expected_status=503)
    assert status == "retry" and progress["done"] == 0 and "cursor_id" not in progress
    assert harness.row(rid)["body_clean"] is None
    harness.app.state.task_context = harness.context
    assert harness.run(job=job)[2]["done"] == 1


def test_retention_uses_later_created_clock(world: tuple[DbPool, list[dict[str, UUID]]]) -> None:
    pool, items = world
    rid = upsert(pool, items[0], replace(record(items[0]), raw_object_uri=None))
    with pool.connection() as db:
        db.execute(
            "update public.source_record set processing_state = 'done', "
            "rehydrated_at = now() - interval '1 year' where id = %s",
            (rid,),
        )
    assert purge_expired_bodies(pool, FakeRawStore()).purged == 0


def test_throttled_mailbox_continues_and_keeps_resume_point(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rid = harness.seed()
    monkeypatch.setattr("firra.http.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if "/messages/one" in request.url.path:
            return httpx.Response(429, json={"error": {"code": "TooManyRequests"}})
        return harness.fake.handler(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        harness.app.state.task_context = replace(harness.context, http=http)
        status, job, progress = harness.run()
    assert status == "continued" and progress["done"] == progress["failed"] == 0
    assert harness.queue.delays == [300] and harness.row(rid)["body_clean"] is None
    harness.app.state.task_context = harness.context
    assert harness.run(job=job)[2]["done"] == 1


def test_a_removed_member_cannot_fetch_mail_again(harness: Harness) -> None:
    rid = harness.seed()
    with harness.pool.connection() as db:
        other = uuid4()  # a workspace keeps an owner: add one before removing the member
        db.execute("insert into auth.users (id) values (%s)", (other,))
        db.execute(
            "insert into public.membership (workspace_id, user_id, role) values (%s, %s, 'owner')",
            (harness.items[0]["workspace"], other),
        )
        db.execute(
            "delete from public.membership where workspace_id = %s and user_id = %s",
            (harness.items[0]["workspace"], harness.items[0]["user"]),
        )
    status, _, progress = harness.run()
    assert status == "succeeded" and progress.get("total", 0) == 0
    assert harness.row(rid)["body_clean"] is None
