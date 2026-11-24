"""Vault ownership, event retention and best-effort raw deletion against local Postgres."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from firra.db.records import RecordInput, RecordRepo
from firra.db.retention import purge_expired_bodies
from firra.db.types import WorkspaceId
from firra.logging import JsonFormatter
from firra.storage.raw import ObjectNotFoundError
from tests.conftest import local_settings

pytestmark = pytest.mark.integration
DbPool = ConnectionPool[Connection[dict[str, Any]]]


class FakeRawStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail = fail

    def put(self, name: str, data: bytes, content_type: str) -> str:
        uri = f"gs://fake/{name}"
        self.objects[uri] = data
        return uri

    def get(self, uri: str) -> bytes:
        if uri not in self.objects:
            raise ObjectNotFoundError(uri)
        return self.objects[uri]

    def delete(self, uri: str) -> None:
        self.deleted.append(uri)
        if self.fail:
            raise OSError("synthetic raw deletion failure")
        self.objects.pop(uri, None)


@pytest.fixture
def world() -> Iterator[tuple[DbPool, list[dict[str, UUID]]]]:
    with ConnectionPool[Connection[dict[str, Any]]](
        local_settings().database_url.get_secret_value(),
        kwargs={"row_factory": dict_row},
        min_size=1,
        max_size=2,
    ) as pool:
        worlds: list[dict[str, UUID]] = []
        with pool.connection() as db:
            for _ in range(2):
                uid = uuid4()
                db.execute("insert into auth.users (id) values (%s)", (uid,))
                ws = db.execute(
                    "insert into public.workspace (name) values ('vault-test') returning id"
                ).fetchone()
                assert ws is not None
                # The mailbox owner is a member: delivery follows membership (migration 26).
                db.execute(
                    "insert into public.membership (workspace_id, user_id, role) "
                    "values (%s, %s, 'owner')",
                    (ws["id"], uid),
                )
                client = db.execute(
                    "insert into public.client (workspace_id, name) "
                    "values (%s, 'test') returning id",
                    (ws["id"],),
                ).fetchone()
                connection = db.execute(
                    "insert into public.connection (workspace_id, provider, "
                    "mode, account_label, external_account_id, owner_user_id) "
                    "values (%s, 'gmail', 'user', 'test', %s, %s) returning id",
                    (ws["id"], str(uid), uid),
                ).fetchone()
                assert client is not None and connection is not None
                worlds.append(
                    {
                        "workspace": ws["id"],
                        "client": client["id"],
                        "connection": connection["id"],
                        "user": uid,
                    }
                )
        # Keep the shared local DB's other synthetic fixtures out of this maintenance batch.
        # A separate transaction also proves FOR UPDATE SKIP LOCKED respects live locks.
        try:
            with psycopg.connect(local_settings().database_url.get_secret_value()) as locked:
                locked.execute(
                    "select id from public.source_record where workspace_id <> all(%s) for update",
                    ([item["workspace"] for item in worlds],),
                )
                try:
                    yield pool, worlds
                finally:
                    locked.rollback()
        finally:
            with pool.connection() as db:
                for item in worlds:
                    db.execute("delete from public.workspace where id = %s", (item["workspace"],))
                    db.execute("delete from auth.users where id = %s", (item["user"],))


def record(item: dict[str, UUID], external_id: str = "one") -> RecordInput:
    return RecordInput(
        connection_id=item["connection"],
        provider="gmail",
        external_id=external_id,
        kind="email",
        client_id=item["client"],
        policy="full",
        thread_key=None,
        occurred_at=datetime.now(UTC),
        direction="in",
        word_count=1,
        metadata={},
        body_clean="synthetic body",
        raw_object_uri="gs://fake/raw/one",
        deep_link=None,
        content_hash=b"first",
    )


def upsert(pool: DbPool, item: dict[str, UUID], value: RecordInput) -> UUID:
    return RecordRepo(pool).upsert_record(
        workspace_id=WorkspaceId(item["workspace"]),
        record=value,
        author_person_id=None,
        participants=[],
    )[0]


@pytest.mark.parametrize(
    ("state", "hours", "recipient", "delivered", "updated_minutes", "purged"),
    [
        ("done", 1, True, True, 60, True),
        ("done", 1, False, False, 60, True),
        ("done", 71, True, False, 60, False),
        ("done", 73, True, False, 60, True),
        ("pending", 72, True, False, 60, False),
        ("pending", 192, True, False, 60, True),
        ("error", 72, True, False, 60, False),
        ("error", 192, True, False, 60, True),
        ("not_applicable", 1, True, True, 60, True),
        ("not_applicable", 1, False, False, 60, True),
        ("pending", 192, True, False, 10, True),  # the 7-day cap has no exception
        ("pending", 192, True, False, 16, True),
    ],
)
def test_retention_matrix_and_workspace_counts(
    world: tuple[DbPool, list[dict[str, UUID]]],
    state: str,
    hours: int,
    recipient: bool,
    delivered: bool,
    updated_minutes: int,
    purged: bool,
) -> None:
    pool, worlds = world
    rid = upsert(pool, worlds[0], record(worlds[0]))
    other = upsert(pool, worlds[1], record(worlds[1]))
    with pool.connection() as db:
        db.execute(
            "update public.source_record set processing_state = %s, "
            "created_at = now() - make_interval(hours => %s), "
            "vault_user_id = %s, vault_delivered_at = %s where id = %s",
            (
                state,
                hours,
                worlds[0]["user"] if recipient else None,
                datetime.now(UTC) if delivered else None,
                rid,
            ),
        )
        # The update trigger stamps now(); disable only within this local test transaction.
        db.execute("alter table public.source_record disable trigger source_record_set_updated_at")
        db.execute(
            "update public.source_record set updated_at = now() - "
            "make_interval(mins => %s) where id = %s",
            (updated_minutes, rid),
        )
        db.execute("alter table public.source_record enable trigger source_record_set_updated_at")
    raw = FakeRawStore()
    result = purge_expired_bodies(pool, raw)
    assert result.purged == int(purged)
    assert result.unprocessed == int(purged and state == "pending")
    assert result.undelivered == int(purged and recipient and not delivered)
    with pool.connection() as db:
        rows = {
            row["id"]: row
            for row in db.execute(
                "select id, body_clean, raw_object_uri, processing_state::text from "
                "public.source_record where id = any(%s)",
                ([rid, other],),
            ).fetchall()
        }
    assert (rows[rid]["body_clean"] is None) == purged
    assert rows[other]["body_clean"] == "synthetic body"
    assert raw.deleted == (["gs://fake/raw/one"] if purged else [])
    if purged and state == "pending":
        assert rows[rid]["processing_state"] == "skipped_purged"


@pytest.mark.parametrize("fail", [False, True])
def test_raw_delete_failure_still_purges(
    world: tuple[DbPool, list[dict[str, UUID]]],
    fail: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool, worlds = world
    raw = FakeRawStore(fail=fail)
    uri = raw.put("raw/test", b"synthetic", "text/plain")
    rid = upsert(pool, worlds[0], replace(record(worlds[0]), raw_object_uri=uri))
    with pool.connection() as db:
        db.execute(
            "update public.source_record set processing_state = 'done', "
            "vault_delivered_at = now() where id = %s",
            (rid,),
        )
    assert purge_expired_bodies(pool, raw).purged == 1
    assert raw.deleted == [uri]
    if fail:
        entries = [
            entry
            for entry in caplog.records
            if entry.getMessage() == "maintenance.raw_delete_failed"
        ]
        assert len(entries) == 1
        output = JsonFormatter().format(entries[0])
        assert str(rid) in output and "OSError" in output
        assert uri not in output and "synthetic raw deletion failure" not in output
    assert (uri in raw.objects) == fail
    with pool.connection() as db:
        row = db.execute(
            "select body_clean, raw_object_uri from public.source_record where id = %s", (rid,)
        ).fetchone()
        assert row is not None and row["body_clean"] is None and row["raw_object_uri"] is None


def test_owner_and_delivery_survive_resync_changed_body_resets_ack(
    world: tuple[DbPool, list[dict[str, UUID]]],
) -> None:
    pool, worlds = world
    item = worlds[0]
    value = record(item)
    rid = upsert(pool, item, value)
    with pool.connection() as db:
        db.execute(
            "update public.source_record set vault_delivered_at = now() where id = %s", (rid,)
        )
        db.execute(
            "update public.connection set owner_user_id = null where id = %s", (item["connection"],)
        )
    upsert(pool, item, value)
    with pool.connection() as db:
        row = db.execute(
            "select vault_user_id, vault_delivered_at from public.source_record where id = %s",
            (rid,),
        ).fetchone()
        assert row is not None
        assert row["vault_user_id"] == item["user"] and row["vault_delivered_at"] is not None
    upsert(pool, item, replace(value, body_clean="changed", content_hash=b"second"))
    with pool.connection() as db:
        row = db.execute(
            "select vault_user_id, vault_delivered_at from public.source_record where id = %s",
            (rid,),
        ).fetchone()
        assert row is not None
        assert row["vault_user_id"] == item["user"] and row["vault_delivered_at"] is None


@pytest.mark.parametrize(
    ("mode", "status", "linked", "expected"),
    [
        ("user", "active", True, True),
        ("user", "active", False, False),
        ("user", "revoked", True, False),
        ("org_delegated", "active", True, False),
        ("upload", "active", True, False),
    ],
)
def test_contributor_owner_and_modes(
    world: tuple[DbPool, list[dict[str, UUID]]],
    mode: str,
    status: str,
    linked: bool,
    expected: bool,
) -> None:
    pool, worlds = world
    item = worlds[0]
    with pool.connection() as db:
        account_column = db.execute(
            "select 1 from information_schema.columns where table_schema = 'public' "
            "and table_name = 'contributor' and column_name = 'user_id'"
        ).fetchone()
        if linked and account_column is None:
            pytest.skip("T61 account migration is not installed; tested after the T68 merge")
        contributor = db.execute(
            "insert into public.contributor (workspace_id, email, status) "
            "values (%s, 'synthetic@example.test', %s) returning id",
            (item["workspace"], status),
        ).fetchone()
        if linked and contributor is not None:
            db.execute(
                "update public.contributor set user_id = %s where id = %s",
                (item["user"], contributor["id"]),
            )
        assert contributor is not None
        db.execute(
            "update public.connection set mode = %s, owner_user_id = null, "
            "owner_contributor_id = %s where id = %s",
            (mode, contributor["id"], item["connection"]),
        )
    rid = upsert(pool, item, record(item))
    with pool.connection() as db:
        row = db.execute(
            "select vault_user_id from public.source_record where id = %s", (rid,)
        ).fetchone()
        assert row is not None
        assert row["vault_user_id"] == (item["user"] if expected else None)


def test_mail_of_a_removed_member_gets_no_recipient(
    world: tuple[DbPool, list[dict[str, UUID]]],
) -> None:
    pool, worlds = world
    item = worlds[0]
    with pool.connection() as db:
        other = uuid4()  # a workspace keeps an owner: add one before removing the member
        db.execute("insert into auth.users (id) values (%s)", (other,))
        db.execute(
            "insert into public.membership (workspace_id, user_id, role) values (%s, %s, 'owner')",
            (item["workspace"], other),
        )
        db.execute(
            "delete from public.membership where workspace_id = %s and user_id = %s",
            (item["workspace"], item["user"]),
        )
    rid = upsert(pool, item, record(item, "after-removal"))
    with pool.connection() as db:
        row = db.execute(
            "select vault_user_id from public.source_record where id = %s", (rid,)
        ).fetchone()
    assert row is not None and row["vault_user_id"] is None
