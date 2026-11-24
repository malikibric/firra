"""Independent oracle for encrypted bodies and the local profile (L1-07o).

Written from the L1-07 brief and ``docs/plans/local-v1.md`` T11, not from
Codex's implementation or tests. ``firra.local.bodycrypt`` exists only on the
implementation branch, so it is imported with ``importorskip``: on ``main``
this file skips cleanly. Run against Codex's branch::

    git worktree add /tmp/fira-scratch-L1-07o origin/codex/L1-07-encrypted-bodies
    cp services/worker/tests/oracle/local/test_encryption_oracle.py \
        /tmp/fira-scratch-L1-07o/services/worker/tests/oracle/local/
    cd /tmp/fira-scratch-L1-07o && FIRRA_LOCAL_PG_BIN=~/firra-tools/engine/postgres/bin \
        uv run pytest services/worker/tests/oracle/local/test_encryption_oracle.py -q
"""

import base64
import json
import logging
import os
import socket
import stat
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool
from pydantic import ValidationError

from firra.clean.email import clean_email
from firra.db.pool import create_pool_for
from firra.db.records import RecordInput, RecordRepo
from firra.db.retention import purge_expired_bodies
from firra.db.types import WorkspaceId
from firra.local.secrets import MissingSecretError, SecretStore
from firra.storage.raw import LocalRawStore
from firra.verify.quotes import BodyIndex, VerifiedQuote

bodycrypt = pytest.importorskip("firra.local.bodycrypt")

needs_bin = pytest.mark.integration

Pool = ConnectionPool[Connection[DictRow]]

PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")
MARKER = "ORACLE-SEKRIT-BODY-9f3a7c2e"
LONG_MARKER = "ORACLE-SEKRIT-LONG-5b81d9f0"


def _key() -> tuple[bytes, str]:
    raw = os.urandom(32)
    return raw, base64.b64encode(raw).decode()


def _provider(secret: str, *, allow_plaintext_migration: bool = False) -> Any:
    store = SecretStore()
    store.set("db_encryption_key", secret)
    return bodycrypt.BodyKeyProvider(store, allow_plaintext_migration=allow_plaintext_migration)


def _bin() -> None:
    if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")


# -- requirement 1: round trip and format (no database) -------------------------


def test_1_token_format_is_versioned_nonce_ciphertext_tag() -> None:
    key, _ = _key()
    token = bodycrypt.encrypt(key, "hello", "ws:r1")
    assert token.startswith("enc:v1:")
    payload = token[len("enc:v1:") :]
    assert set(payload) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
    sealed = base64.b64decode(payload)
    assert len(sealed) == 12 + len(b"hello") + 16


def test_1_same_plaintext_encrypts_distinctly_1000_times() -> None:
    key, _ = _key()
    tokens = {bodycrypt.encrypt(key, "same body", "ws:r1") for _ in range(1000)}
    assert len(tokens) == 1000


def test_1_empty_large_unicode_and_nul_round_trip() -> None:
    key, _ = _key()
    bodies = ["", "x" * (1024 * 1024), "Grüße — naši klijenti 🎉\x00trailer"]
    for body in bodies:
        assert bodycrypt.decrypt(key, bodycrypt.encrypt(key, body, "ws:r1"), "ws:r1") == body


# -- requirement 2: authentication (no database) ---------------------------------


def test_2_every_byte_position_is_authenticated() -> None:
    key, _ = _key()
    token = bodycrypt.encrypt(key, "hi", "ws:r1")
    prefix, payload = token[: len("enc:v1:")], token[len("enc:v1:") :]
    sealed = bytearray(base64.b64decode(payload))
    assert len(sealed) == 12 + 2 + 16
    for i in range(len(sealed)):
        mutated = bytearray(sealed)
        mutated[i] ^= 1
        forged = prefix + base64.b64encode(bytes(mutated)).decode()
        with pytest.raises(bodycrypt.BodyCryptError):
            bodycrypt.decrypt(key, forged, "ws:r1")


def test_2_wrong_key_and_swapped_aad_fail() -> None:
    key, _ = _key()
    other = os.urandom(32)
    token = bodycrypt.encrypt(key, "secret body", "ws:r1")
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.decrypt(other, token, "ws:r1")
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.decrypt(key, token, "ws:r2")
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.decrypt(key, token, "other-ws:r1")


def test_2_truncated_extended_wrong_version_and_prefix_fail() -> None:
    key, _ = _key()
    token = bodycrypt.encrypt(key, "secret body", "ws:r1")
    payload = base64.b64decode(token[len("enc:v1:") :])
    short = "enc:v1:" + base64.b64encode(payload[:-16]).decode()
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.decrypt(key, short, "ws:r1")
    long = "enc:v1:" + base64.b64encode(payload + b"\x00").decode()
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.decrypt(key, long, "ws:r1")
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.decrypt(key, "enc:v2:" + token[len("enc:v1:") :], "ws:r1")


def test_2_bad_keys_and_missing_slot_fail_typed() -> None:
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.encrypt(os.urandom(16), "x", "ws:r1")
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.encrypt(os.urandom(33), "x", "ws:r1")
    with pytest.raises(MissingSecretError):
        bodycrypt.BodyKeyProvider(SecretStore()).key()
    bad = SecretStore()
    bad.set("db_encryption_key", "!!!not-base64!!!")
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.BodyKeyProvider(bad).key()
    short = SecretStore()
    short.set("db_encryption_key", base64.b64encode(os.urandom(16)).decode())
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.BodyKeyProvider(short).key()


def test_2_plaintext_needs_the_migration_flag() -> None:
    _, secret = _key()
    locked = _provider(secret)
    migrating = _provider(secret, allow_plaintext_migration=True)

    class FakePool:
        pass

    pool = FakePool()
    bodycrypt.bind(pool, locked)
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.read_body(pool, "ws", "r1", "plain old body")
    bodycrypt.bind(pool, migrating)
    assert bodycrypt.read_body(pool, "ws", "r1", "plain old body") == "plain old body"


def test_2_replaced_key_leaves_old_rows_unreadable() -> None:
    key, first_secret = _key()
    token = bodycrypt.encrypt(key, "old row", "ws:r1")
    _, second_secret = _key()
    store = SecretStore()
    store.set("db_encryption_key", first_secret)
    provider = bodycrypt.BodyKeyProvider(store)
    assert provider.key() == key
    store.set("db_encryption_key", second_secret)
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.decrypt(provider.key(), token, "ws:r1")


# -- requirement 3: error hygiene (no database) ----------------------------------


def test_3_errors_and_logs_carry_no_secrets(caplog: pytest.LogCaptureFixture) -> None:
    key, secret = _key()
    plaintext = f"plaintext {MARKER}"
    token = bodycrypt.encrypt(key, plaintext, "ws:r1")
    seen: list[BaseException] = []
    calls: list[Callable[[], None]] = [
        lambda: bodycrypt.decrypt(os.urandom(32), token, "ws:r1"),
        lambda: bodycrypt.decrypt(key, token, "ws:other"),
        lambda: bodycrypt.decrypt(key, token[:-4] + "AAAA", "ws:r1"),
        lambda: bodycrypt.BodyKeyProvider(SecretStore()).key(),
    ]
    for bad in calls:
        with caplog.at_level(logging.DEBUG), pytest.raises(Exception) as excinfo:
            bad()
        seen.append(excinfo.value)
    assert seen, "expected failing calls"
    b64key = base64.b64encode(key).decode()
    for exc in seen:
        for text in (str(exc), repr(exc)):
            assert b64key not in text and MARKER not in text and token not in text
    assert b64key not in caplog.text and MARKER not in caplog.text and token not in caplog.text
    store = SecretStore()
    store.set("db_encryption_key", secret)
    assert secret not in repr(store)


# -- database fixtures (bundled Postgres) ----------------------------------------


@pytest.fixture
def cluster() -> Iterator[tuple[str, Pool]]:
    _bin()
    assert PG_BIN is not None
    from firra.local.db import migrate
    from firra.local.pg import PostgresSupervisor

    root = Path(tempfile.mkdtemp(prefix="firra-oracle-enc-", dir="/tmp"))
    supervisor = PostgresSupervisor(Path(PG_BIN), root / "data", root / "run")
    supervisor.ensure_cluster()
    supervisor.start()
    migrate(supervisor.conninfo)
    pool = create_pool_for(supervisor.conninfo, min_size=1, max_size=4)
    pool.open()
    try:
        yield supervisor.conninfo, pool
    finally:
        pool.close()
        supervisor.stop()


@pytest.fixture
def bound(cluster: tuple[str, Pool]) -> Iterator[tuple[Pool, Any, WorkspaceId]]:
    _, pool = cluster
    _, secret = _key()
    provider = _provider(secret)
    bodycrypt.bind(pool, provider)
    with pool.connection() as conn:
        workspace = conn.execute(
            "insert into public.workspace (name) values ('oracle') returning id"
        ).fetchone()
        assert workspace is not None
        ws = WorkspaceId(workspace["id"])
        client = conn.execute(
            "insert into public.client (workspace_id, name, policy)"
            " values (%s, 'oracle', 'full') returning id",
            (ws,),
        ).fetchone()
        connection = conn.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label)"
            " values (%s, 'email_upload', 'upload', 'oracle') returning id",
            (ws,),
        ).fetchone()
        assert client is not None and connection is not None
        conn.commit()
    yield pool, provider, ws


def _record(
    connection_id: UUID, client_id: UUID, external: str, body: str, occurred: datetime | None = None
) -> RecordInput:
    return RecordInput(
        connection_id=connection_id,
        provider="email_upload",
        external_id=external,
        kind="email",
        client_id=client_id,
        policy="full",
        thread_key="oracle-thread",
        occurred_at=occurred or datetime(2026, 10, 5, 9, tzinfo=UTC),
        direction="in",
        word_count=len(body.split()),
        metadata={},
        body_clean=body,
        raw_object_uri=None,
        deep_link=None,
        content_hash=b"oracle",
    )


def _connection_and_client(pool: Pool) -> tuple[Any, Any]:
    with pool.connection() as conn:
        rows = conn.execute(
            "select c.id as cid, cn.id as nid from public.client c"
            " join public.connection cn on cn.workspace_id = c.workspace_id"
            " where c.name = 'oracle' limit 1"
        ).fetchone()
        assert rows is not None
        return rows["nid"], rows["cid"]


# -- requirement 4: database proof -------------------------------------------------


@needs_bin
def test_4_stored_bodies_are_ciphertext_only(
    bound: tuple[Pool, Any, WorkspaceId],
) -> None:
    """Spec: body_clean holds enc:v1: and no plaintext fragment, anywhere."""
    pool, _, ws = bound
    body = f"Meeting notes {MARKER} about the renewal contract"
    connection_id, client_id = _connection_and_client(pool)
    repo = RecordRepo(pool)
    record_id, _ = repo.upsert_record(
        workspace_id=ws,
        record=_record(connection_id, client_id, "oracle-1", body),
        author_person_id=None,
        participants=[],
        event=None,
    )
    with pool.connection() as conn:
        stored = conn.execute(
            "select body_clean from public.source_record where id = %s", (record_id,)
        ).fetchone()
        assert stored is not None and stored["body_clean"].startswith("enc:v1:")
        dump = conn.execute(
            "select string_agg(coalesce(body_clean, ''), '|') from public.source_record"
        ).fetchone()
        assert dump is not None and MARKER not in dump["string_agg"]
    assert PG_BIN is not None
    dump_proc = subprocess.run(  # noqa: S603 - throwaway cluster dump in tests
        [
            f"{PG_BIN}/pg_dump",
            "--host",
            _socket_dir(pool),
            "--username",
            "postgres",
            "--dbname",
            "firra",
            "--table",
            "public.source_record",
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "en_US.UTF-8"},
    )
    assert dump_proc.returncode == 0
    assert MARKER not in dump_proc.stdout


def _socket_dir(pool: Pool) -> str:
    with pool.connection() as conn:
        row = conn.execute("show unix_socket_directories").fetchone()
        assert row is not None
        return str(row["unix_socket_directories"]).split(",")[0].strip().strip('"')


@needs_bin
def test_4_data_directory_files_hold_no_plaintext(
    bound: tuple[Pool, Any, WorkspaceId], cluster: tuple[str, Pool]
) -> None:
    """Spec: a stolen file is useless — the marker appears in no file on disk."""
    pool, _, ws = bound
    connection_id, client_id = _connection_and_client(pool)
    RecordRepo(pool).upsert_record(
        workspace_id=ws,
        record=_record(connection_id, client_id, "oracle-2", f"disk check {MARKER}"),
        author_person_id=None,
        participants=[],
        event=None,
    )
    with pool.connection() as conn:
        conn.execute("checkpoint")
    conninfo, _ = cluster
    data_root = Path(tempfile.gettempdir())
    needle = MARKER.encode()
    assert needle not in _read_cluster_files(conninfo)
    _ = data_root


def _read_cluster_files(conninfo: str) -> bytes:
    import re

    match = re.search(r"host=([^\s]+)", conninfo)
    assert match is not None
    found = b""
    for path in Path(match.group(1)).parent.rglob("*"):
        if path.is_file():
            try:
                if path.stat().st_size > 64 * 1024 * 1024:
                    continue
                found += path.read_bytes()
            except OSError:
                continue
    return found


@needs_bin
def test_4_ciphertext_copied_to_another_row_fails_to_read(
    bound: tuple[Pool, Any, WorkspaceId],
) -> None:
    """Spec: AAD binds workspace_id:record_id — a swapped body must not decrypt."""
    pool, _, ws = bound
    connection_id, client_id = _connection_and_client(pool)
    repo = RecordRepo(pool)
    first, _ = repo.upsert_record(
        workspace_id=ws,
        record=_record(connection_id, client_id, "oracle-3a", f"first {MARKER}"),
        author_person_id=None,
        participants=[],
        event=None,
    )
    second, _ = repo.upsert_record(
        workspace_id=ws,
        record=_record(connection_id, client_id, "oracle-3b", f"second {MARKER}"),
        author_person_id=None,
        participants=[],
        event=None,
    )
    with pool.connection() as conn:
        token = conn.execute(
            "select body_clean from public.source_record where id = %s", (first,)
        ).fetchone()
        assert token is not None
        conn.execute(
            "update public.source_record set body_clean = %s where id = %s",
            (token["body_clean"], second),
        )
        conn.commit()
    with pytest.raises(bodycrypt.BodyCryptError):
        bodycrypt.read_body(pool, ws, second, token["body_clean"])


@needs_bin
def test_4_resync_keeps_row_and_aad_new_body_new_ciphertext(
    bound: tuple[Pool, Any, WorkspaceId],
) -> None:
    """Spec: unchanged re-sync keeps the row id; a changed message gets a new token."""
    pool, _, ws = bound
    connection_id, client_id = _connection_and_client(pool)
    repo = RecordRepo(pool)
    first_id, _ = repo.upsert_record(
        workspace_id=ws,
        record=_record(connection_id, client_id, "oracle-4", f"v1 {MARKER}"),
        author_person_id=None,
        participants=[],
        event=None,
    )
    same_id, _ = repo.upsert_record(
        workspace_id=ws,
        record=_record(connection_id, client_id, "oracle-4", f"v1 {MARKER}"),
        author_person_id=None,
        participants=[],
        event=None,
    )
    assert same_id == first_id
    with pool.connection() as conn:
        before = conn.execute(
            "select body_clean from public.source_record where id = %s", (first_id,)
        ).fetchone()
    changed_id, _ = repo.upsert_record(
        workspace_id=ws,
        record=_record(connection_id, client_id, "oracle-4", f"v2 {MARKER} changed"),
        author_person_id=None,
        participants=[],
        event=None,
    )
    assert changed_id == first_id
    with pool.connection() as conn:
        after = conn.execute(
            "select body_clean from public.source_record where id = %s", (first_id,)
        ).fetchone()
    assert before is not None and after is not None and before["body_clean"] != after["body_clean"]
    assert bodycrypt.read_body(pool, ws, first_id, after["body_clean"]) == f"v2 {MARKER} changed"


@needs_bin
def test_4_purge_nulls_encrypted_bodies(bound: tuple[Pool, Any, WorkspaceId]) -> None:
    """Spec: purge keeps working on ciphertext (nulls bodies, keeps rows)."""
    pool, _, ws = bound
    connection_id, client_id = _connection_and_client(pool)
    repo = RecordRepo(pool)
    record_id, _ = repo.upsert_record(
        workspace_id=ws,
        record=_record(connection_id, client_id, "oracle-5", f"purge me {MARKER}"),
        author_person_id=None,
        participants=[],
        event=None,
    )
    with pool.connection() as conn:
        conn.execute(
            "update public.source_record set created_at = now() - interval '400 days'"
            " where id = %s",
            (record_id,),
        )
        conn.commit()
    raw = LocalRawStore(Path(tempfile.mkdtemp(prefix="firra-oracle-raw-")))
    result = purge_expired_bodies(pool, raw)
    assert result.purged >= 1
    with pool.connection() as conn:
        row = conn.execute(
            "select body_clean from public.source_record where id = %s", (record_id,)
        ).fetchone()
        assert row is not None and row["body_clean"] is None


@needs_bin
def test_4_policy_still_removes_bodies(bound: tuple[Pool, Any, WorkspaceId]) -> None:
    """Spec: metadata_only drops bodies but keeps rows; excluded deletes rows."""
    pool, _, ws = bound
    connection_id, client_id = _connection_and_client(pool)
    repo = RecordRepo(pool)
    meta_id, _ = repo.upsert_record(
        workspace_id=ws,
        record=_record(connection_id, client_id, "oracle-6a", f"meta {MARKER}"),
        author_person_id=None,
        participants=[],
        event=None,
    )
    gone_id, _ = repo.upsert_record(
        workspace_id=ws,
        record=_record(connection_id, client_id, "oracle-6b", f"gone {MARKER}"),
        author_person_id=None,
        participants=[],
        event=None,
    )
    with pool.connection() as conn:
        owner = conn.execute(
            "insert into auth.users (email) values ('oracle@example.com') returning id"
        ).fetchone()
        assert owner is not None
        conn.execute(
            "insert into public.membership (workspace_id, user_id, role) values (%s, %s, 'owner')",
            (ws, owner["id"]),
        )
        conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(owner["id"]),))
        meta_client = conn.execute(
            "insert into public.client (workspace_id, name, policy)"
            " values (%s, 'oracle-meta', 'full') returning id",
            (ws,),
        ).fetchone()
        gone_client = conn.execute(
            "insert into public.client (workspace_id, name, policy)"
            " values (%s, 'oracle-gone', 'full') returning id",
            (ws,),
        ).fetchone()
        assert meta_client is not None and gone_client is not None
        conn.execute(
            "update public.source_record set client_id = %s where id = %s",
            (meta_client["id"], meta_id),
        )
        conn.execute(
            "update public.source_record set client_id = %s where id = %s",
            (gone_client["id"], gone_id),
        )
        conn.execute("select public.set_client_policy(%s, 'metadata_only')", (meta_client["id"],))
        conn.execute("select public.set_client_policy(%s, 'excluded')", (gone_client["id"],))
        conn.commit()
        meta_row = conn.execute(
            "select body_clean from public.source_record where id = %s", (meta_id,)
        ).fetchone()
        assert meta_row is not None and meta_row["body_clean"] is None
        gone_row = conn.execute(
            "select id from public.source_record where id = %s", (gone_id,)
        ).fetchone()
        assert gone_row is None


@needs_bin
def test_4_downstream_sees_plaintext_but_no_packet_processor_on_branch(
    bound: tuple[Pool, Any, WorkspaceId],
) -> None:
    """Spec: cleaning and quote verification see plaintext. The packet processor
    with a FakeExecutor (brief req 4) does not exist on this branch — L3-01
    merged on main after the fork — so this pins cleaning + verification."""
    pool, _, ws = bound
    connection_id, client_id = _connection_and_client(pool)
    quote = f"the renewal tslil shake hands {LONG_MARKER} next Tuesday morning"
    body = f"Hi Ana, just confirming {quote}. Best, Ruth"
    record_id, _ = RecordRepo(pool).upsert_record(
        workspace_id=ws,
        record=_record(connection_id, client_id, "oracle-7", body),
        author_person_id=None,
        participants=[],
        event=None,
    )
    with pool.connection() as conn:
        stored = conn.execute(
            "select body_clean from public.source_record where id = %s", (record_id,)
        ).fetchone()
        assert stored is not None
        plaintext = bodycrypt.read_body(pool, ws, record_id, stored["body_clean"])
    cleaned = clean_email(plaintext)
    assert LONG_MARKER in cleaned.body
    verdict = BodyIndex(cleaned.body).verify(quote)
    assert isinstance(verdict, VerifiedQuote), f"quote rejected: {verdict!r}"


# -- requirement 5: secrets on disk (real engine over the control socket) ---------


def _open_files() -> dict[str, bytes]:
    """Path -> head bytes for every regular file this process holds open (macOS)."""
    found: dict[str, bytes] = {}
    fd_dir = Path("/dev/fd")
    if not fd_dir.is_dir():
        return found
    for fd in fd_dir.iterdir():
        try:
            target = fd.readlink()
        except OSError:
            continue
        target_path = Path(target)
        if str(target).startswith("/dev/") or not target_path.is_file():
            continue
        try:
            with target_path.open("rb") as handle:
                found[str(target)] = handle.read(4 * 1024 * 1024)
        except OSError:
            continue
    return found


@needs_bin
def test_5_engine_run_leaves_no_key_or_token_on_disk(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec: key + provider tokens travel the socket into memory only."""
    engine_mod = pytest.importorskip("firra.local.engine")
    _bin()
    assert PG_BIN is not None
    engine_cls, paths_cls = engine_mod.Engine, engine_mod.Paths

    _ = engine_mod
    root = tmp_path / "home"
    # Unix sockets cap at ~100 bytes; pytest tmp paths are already ~90.
    run = Path(tempfile.mkdtemp(prefix="firra-oracle-run-", dir="/tmp"))
    paths = paths_cls.under(root, Path(PG_BIN), run)
    engine = engine_cls(paths, version="oracle", expected_app_pid=None)
    key_raw, key_b64 = _key()
    google_token = f"ya29.oracle-{os.urandom(8).hex()}"
    slack_token = f"xoxp-oracle-{os.urandom(8).hex()}"
    thread = threading.Thread(target=engine.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if paths.control_socket.exists():
                break
            threading.Event().wait(0.05)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(60)
        sock.connect(str(paths.control_socket))
        reader = sock.makefile("rb")

        def send(message: object) -> None:
            sock.sendall((json.dumps(message) + "\n").encode())

        def read() -> Any:
            line = reader.readline(17 * 1024)
            assert line
            return json.loads(line)

        with caplog.at_level(logging.DEBUG):
            send({"type": "hello", "protocol": 1, "app_version": "oracle"})
            assert read()["type"] == "hello_ack"
            send({"type": "secret_set", "name": "db_encryption_key", "value": key_b64})
            send(
                {"type": "secret_set", "name": "google_refresh_token:conn1", "value": google_token}
            )
            send({"type": "secret_set", "name": "slack_user_token:conn1", "value": slack_token})
            send({"type": "secrets_complete"})
            assert read()["type"] == "ready"
            runtime = engine._runtime
            assert runtime is not None
            with runtime.pool.connection() as conn:
                ws_row = conn.execute(
                    "insert into public.workspace (name) values ('oracle5') returning id"
                ).fetchone()
                assert ws_row is not None
                ws = WorkspaceId(ws_row["id"])
                client = conn.execute(
                    "insert into public.client (workspace_id, name, policy)"
                    " values (%s, 'o5', 'full') returning id",
                    (ws,),
                ).fetchone()
                connection = conn.execute(
                    "insert into public.connection (workspace_id, provider, mode, account_label)"
                    " values (%s, 'email_upload', 'upload', 'o5') returning id",
                    (ws,),
                ).fetchone()
                assert client is not None and connection is not None
                conn.commit()
            runtime.context.records.upsert_record(
                workspace_id=ws,
                record=_record(connection["id"], client["id"], "oracle-8", f"engine run {MARKER}"),
                author_person_id=None,
                participants=[],
                event=None,
            )
            send({"type": "shutdown"})
        reader.close()
        sock.close()
    finally:
        engine.stop()
        thread.join(timeout=60)
    needles = [key_raw, key_b64.encode(), google_token.encode(), slack_token.encode()]
    haystacks: list[bytes] = []
    for path in root.rglob("*"):
        if path.is_file():
            try:
                if path.stat().st_size < 32 * 1024 * 1024:
                    haystacks.append(path.read_bytes())
            except OSError:
                continue
    haystacks.extend(_open_files().values())
    haystacks.append(caplog.text.encode())
    for needle in needles:
        assert all(needle not in hay for hay in haystacks), "secret bytes found outside memory"


# -- requirement 6: local profile (no database) ------------------------------------


def test_6_local_profile_needs_no_cloud_settings(tmp_path: Path) -> None:
    """Spec: FIRRA_PROFILE=local builds settings with no GCS/KMS/task/Vertex fields; dirs 0700."""
    profile = pytest.importorskip("firra.local.profile")
    home = tmp_path / "Firra"
    settings = profile.build_settings("host=/tmp/oracle user=postgres dbname=firra", home)
    assert settings.profile == "local"
    assert settings.kms_mode == "none"
    assert settings.task_auth_mode == "none"
    assert settings.local_task_secret is None
    assert settings.local_kek_hex is None
    assert settings.kms_key_name is None
    assert settings.raw_bucket is None
    assert settings.gcp_project_id is None
    assert settings.vertex_location is None
    assert settings.vertex_embed_location is None
    assert settings.google_client_id is None
    assert settings.processing_enabled is False
    assert settings.local_raw_dir == str(home / "raw")
    for directory in (home, home / "raw", home / "log", home / "run"):
        assert directory.is_dir()
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_6_symlinked_profile_directory_is_refused(tmp_path: Path) -> None:
    """Spec: profile directories must not be symlinks."""
    profile = pytest.importorskip("firra.local.profile")
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ValueError):
        profile.build_settings("host=/tmp/oracle user=postgres dbname=firra", link)


def test_6_cloud_validation_unchanged() -> None:
    """Spec: local profile changes nothing about cloud validation."""
    from firra.settings import Settings

    with pytest.raises(ValidationError):
        Settings.model_validate({"profile": "cloud"})


# -- requirement 7: concurrency (bundled Postgres) ---------------------------------


@needs_bin
def test_7_concurrent_upserts_leave_one_readable_row(
    bound: tuple[Pool, Any, WorkspaceId],
) -> None:
    """Spec: 8 threads, same record → one row, one readable ciphertext."""
    pool, _, ws = bound
    connection_id, client_id = _connection_and_client(pool)
    body = f"concurrent {MARKER}"

    def once(_: int) -> UUID:
        record_id, _ = RecordRepo(pool).upsert_record(
            workspace_id=ws,
            record=_record(connection_id, client_id, "oracle-9", body),
            author_person_id=None,
            participants=[],
            event=None,
        )
        return record_id

    with ThreadPoolExecutor(max_workers=8) as workers:
        ids = list(workers.map(once, range(8)))
    assert len(set(ids)) == 1
    with pool.connection() as conn:
        count = conn.execute(
            "select count(*) from public.source_record where external_id = 'oracle-9'"
        ).fetchone()
        assert count is not None and count["count"] == 1
        stored = conn.execute(
            "select id, body_clean from public.source_record where external_id = 'oracle-9'"
        ).fetchone()
        assert stored is not None
    assert bodycrypt.read_body(pool, ws, stored["id"], stored["body_clean"]) == body
