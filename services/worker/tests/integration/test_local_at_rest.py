"""Source-layer S0: no plain provider payloads on disk, subjects and titles encrypted in the DB."""

import base64
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from firra.db.local_secure import protect_legacy_fields
from firra.db.processing import ProcessingRepo
from firra.db.records import RecordInput
from firra.db.types import WorkspaceId
from firra.local.bodycrypt import read_field
from firra.local.engine import Engine, Paths
from firra.local.routes.evidence_builders import shape_evidence
from firra.storage.raw import NoRawStore, ObjectNotFoundError

pytestmark = pytest.mark.integration
PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")
SUBJECT = "Zephyrine renewal pricing worries"
TITLE = "Zephyrine quarterly steering"
BODY = "We are worried that the Zephyrine renewal pricing will not hold this year."
LEGACY = "Plainlegacy subject Quillmore"


def test_no_raw_store_keeps_nothing() -> None:
    store = NoRawStore()
    store.put("x", b"secret", "application/json")
    with pytest.raises(ObjectNotFoundError):
        store.get("gs://local/x")


def test_subjects_titles_encrypted_raw_dir_removed_and_legacy_repaired() -> None:
    if not PG_BIN:
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    with tempfile.TemporaryDirectory(prefix="firra-rest-", dir="/tmp") as root:
        paths = Paths.under(Path(root) / "home", Path(PG_BIN), Path(root) / "run")
        legacy_raw = paths.home / "raw"
        legacy_raw.mkdir(parents=True)
        (legacy_raw / "old.json").write_text(LEGACY)
        engine = Engine(paths, version="fixture", expected_app_pid=None)
        engine.secrets.set("db_encryption_key", base64.b64encode(os.urandom(32)).decode())
        try:
            # a legacy row written before S0, inserted before the runtime starts
            engine._pg.ensure_ready()
            engine._start_services()
            runtime = engine._runtime
            assert runtime is not None
            assert not legacy_raw.exists()
            with psycopg.connect(engine._pg.conninfo, row_factory=dict_row, autocommit=True) as db:
                ws = WorkspaceId(engine.identity.workspace_id)
                client = db.execute(
                    "insert into public.client (workspace_id,name,policy) values (%s,'z','full')"
                    " returning id",
                    (ws,),
                ).fetchone()
                conn_row = db.execute(
                    "insert into public.connection (workspace_id,provider,mode,account_label)"
                    " values (%s,'email_upload','upload','fixture') returning id",
                    (ws,),
                ).fetchone()
                assert client is not None and conn_row is not None
                legacy = db.execute(
                    "insert into public.source_record (workspace_id, connection_id, provider,"
                    " external_id, kind, client_id, policy_at_ingest, occurred_at, direction,"
                    " metadata, raw_object_uri) values (%s,%s,'email_upload','legacy','email',%s,"
                    "'full',now(),'in',%s,'gs://local/old.json') returning id",
                    (ws, conn_row["id"], client["id"], Jsonb({"subject": LEGACY})),
                ).fetchone()
                assert legacy is not None

            repo = runtime.context.records

            def record(external: str, kind: str, metadata: dict[str, Any], body: str | None) -> Any:
                return repo.upsert_record(
                    workspace_id=ws,
                    author_person_id=None,
                    participants=[],
                    record=RecordInput(
                        connection_id=conn_row["id"],
                        provider="email_upload",
                        external_id=external,
                        kind=kind,  # type: ignore[arg-type]
                        client_id=client["id"],
                        policy="full",
                        thread_key="t1",
                        occurred_at=datetime(2026, 9, 30, tzinfo=UTC),
                        direction="in",
                        word_count=12,
                        metadata=metadata,
                        body_clean=body,
                        raw_object_uri=None,
                        deep_link=None,
                        content_hash=None,
                    ),
                )

            mail_id, _ = record("m1", "email", {"subject": SUBJECT}, BODY)
            event_id, _ = record("e1", "calendar_event", {"title": TITLE, "duration_min": 30}, None)
            assert protect_legacy_fields(runtime.pool) == 1

            with psycopg.connect(engine._pg.conninfo, row_factory=dict_row) as db:
                rows = {
                    r["external_id"]: r
                    for r in db.execute(
                        "select id, external_id, metadata, body_clean, raw_object_uri"
                        " from public.source_record where workspace_id = %s",
                        (ws,),
                    )
                }
            assert rows["m1"]["metadata"]["subject"].startswith("enc:v1:")
            assert rows["m1"]["body_clean"].startswith("enc:v1:")
            assert rows["e1"]["metadata"]["title"].startswith("enc:v1:")
            assert rows["e1"]["metadata"]["duration_min"] == 30
            assert rows["legacy"]["metadata"]["subject"].startswith("enc:v1:")
            assert rows["legacy"]["raw_object_uri"] is None
            # readers see plaintext through the key; a value cannot move between fields
            pool = runtime.pool
            assert (
                read_field(pool, ws, mail_id, "subject", rows["m1"]["metadata"]["subject"])
                == SUBJECT
            )
            assert read_field(pool, ws, event_id, "title", rows["e1"]["metadata"]["title"]) == TITLE
            with pytest.raises(Exception):  # noqa: B017 - any decryption failure is correct
                read_field(pool, ws, event_id, "subject", rows["e1"]["metadata"]["title"])
            pending = ProcessingRepo(pool).pending(workspace_id=ws, limit=10)
            assert [p.subject for p in pending if p.id == mail_id] == [SUBJECT]
            shaped = shape_evidence(
                {
                    "id": mail_id,
                    "client_id": client["id"],
                    "quote": "worried that the Zephyrine renewal",
                    "locator": {"start": 0, "end": 10},
                    "deep_link": None,
                    "occurred_at": datetime(2026, 9, 30, tzinfo=UTC),
                    "source_record_id": mail_id,
                    "metadata": rows["m1"]["metadata"],
                    "display_name": None,
                    "side": None,
                    "role_title": None,
                },
                pool=pool,
                workspace_id=ws,
            )
            assert shaped["subject"] == SUBJECT
            # nothing readable on disk: database files after a checkpoint and the whole home
            with psycopg.connect(engine._pg.conninfo, autocommit=True) as plain:
                plain.execute("checkpoint")
            # LEGACY was written in plain text before the repair (old WAL pages of a dev database
            # may hold it); everything this engine writes must be ciphertext.
            for needle in (SUBJECT, TITLE, BODY):
                found = subprocess.run(  # noqa: S603
                    ["/usr/bin/grep", "-ral", "--", needle, str(paths.home)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert found.stdout == "", f"{needle!r} on disk: {found.stdout}"
        finally:
            engine._stop_services()
