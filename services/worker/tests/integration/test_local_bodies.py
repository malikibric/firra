"""Repository encryption, stable upserts, row binding, purge and no keys on disk."""

import base64
import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.db.processing import ProcessingRepo
from firra.db.records import RecordInput
from firra.db.retention import purge_expired_bodies
from firra.db.types import WorkspaceId
from firra.local.bodycrypt import BodyCryptError
from firra.local.engine import Engine, Paths

pytestmark = pytest.mark.integration
PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")


def test_engine_repositories_encrypt_and_keep_secrets_in_memory() -> None:
    if not PG_BIN:
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    with tempfile.TemporaryDirectory(prefix="firra-bodies-", dir="/tmp") as root:
        paths = Paths.under(Path(root) / "home", Path(PG_BIN), Path(root) / "run")
        engine = Engine(paths, version="fixture", expected_app_pid=None)
        key = os.urandom(32)
        secret = base64.b64encode(key).decode()
        engine.secrets.set("db_encryption_key", secret)
        try:
            engine._start_services()
            runtime = engine._runtime
            assert runtime is not None
            with psycopg.connect(
                engine._pg.conninfo, row_factory=dict_row, autocommit=True
            ) as conn:
                ws_row = conn.execute(
                    "insert into public.workspace (name) values ('body fixture') returning id"
                ).fetchone()
                assert ws_row is not None
                ws = WorkspaceId(ws_row["id"])
                client = conn.execute(
                    "insert into public.client (workspace_id,name,policy) "
                    "values (%s,'fixture','full') returning id",
                    (ws,),
                ).fetchone()
                connection = conn.execute(
                    "insert into public.connection (workspace_id,provider,mode,account_label) "
                    "values (%s,'email_upload','upload','fixture') returning id",
                    (ws,),
                ).fetchone()
                assert client is not None and connection is not None

                def record(external: str, hours: int) -> RecordInput:
                    return RecordInput(
                        connection_id=connection["id"],
                        provider="email_upload",
                        external_id=external,
                        kind="email",
                        client_id=client["id"],
                        policy="full",
                        thread_key="thread",
                        occurred_at=datetime(2026, 10, 5, 9, tzinfo=UTC) + timedelta(hours=hours),
                        direction="in",
                        word_count=3,
                        metadata={},
                        body_clean="SYNTHETIC_PRIVATE_BODY",
                        raw_object_uri=None,
                        deep_link=None,
                        content_hash=b"fixture",
                    )

                ids = []
                for external, hours in [("first", 0), ("second", 1)]:
                    rid, inserted = runtime.context.records.upsert_record(
                        workspace_id=ws,
                        record=record(external, hours),
                        author_person_id=None,
                        participants=[],
                    )
                    assert inserted
                    ids.append(rid)
                again, inserted = runtime.context.records.upsert_record(
                    workspace_id=ws,
                    record=record("first", 0),
                    author_person_id=None,
                    participants=[],
                )
                assert again == ids[0] and not inserted
                psql = subprocess.run(  # noqa: S603 — synthetic local data, bundled executable
                    [
                        str(Path(PG_BIN) / "psql"),
                        "-X",
                        "-qAt",
                        "-d",
                        engine._pg.conninfo,
                        "-c",
                        "select body_clean from public.source_record",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert psql.stdout.count("enc:v1:") == 2
                assert "SYNTHETIC_PRIVATE_BODY" not in psql.stdout
                repo = ProcessingRepo(runtime.pool)
                records = repo.pending(workspace_id=ws, limit=10)
                assert len(records) == 2
                assert all(r.body_clean == "SYNTHETIC_PRIVATE_BODY" for r in records)
                assert repo.thread_context(workspace_id=ws, record=records[1], limit=2) == [
                    "SYNTHETIC_PRIVATE_BODY"
                ]
                assert repo.save(
                    workspace_id=ws,
                    record=records[0],
                    cleaned_body="CLEANED_FIXTURE",
                    items=[],
                    model="fixture",
                    prompt_version="fixture",
                )
                # Copy a valid ciphertext into another row: the AAD must reject it.
                conn.execute(
                    "update public.source_record set body_clean=(select body_clean "
                    "from public.source_record where id=%s) where id=%s",
                    (ids[0], ids[1]),
                )
                with pytest.raises(BodyCryptError):
                    repo.pending(workspace_id=ws, limit=10)
                conn.execute(
                    "update public.source_record set created_at=now()-interval '8 days' "
                    "where workspace_id=%s",
                    (ws,),
                )
                assert purge_expired_bodies(runtime.pool, runtime.context.raw).purged == 2
                assert all(
                    row["body_clean"] is None
                    for row in conn.execute(
                        "select body_clean from public.source_record"
                    ).fetchall()
                )
        finally:
            engine._stop_services()
        for path in Path(root).rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                assert key not in data and secret.encode() not in data
