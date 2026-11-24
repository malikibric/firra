"""Actual Engine, control socket, encrypted bundled PG and fake Google; no live provider calls."""

import dataclasses
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from psycopg.rows import DictRow

from firra.connectors.google.local_tokens import LocalGoogleTokens
from firra.db.local_identity import LocalIdentity
from firra.local.control import ControlServer
from firra.local.engine import Engine, Paths
from firra.local.profile import LocalRuntime
from firra.local.secrets import SecretStore
from firra.settings import Settings
from tests.fake_google import FakeGoogle, event
from tests.mail import email_bytes

pytestmark = pytest.mark.integration
PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")


def wait_for(check: Any) -> Any:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        threading.Event().wait(0.025)
    raise AssertionError("local sync did not finish")


def test_engine_google_connect_recovery_rotation_revocation_and_no_tokens(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if not PG_BIN:
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    fake = FakeGoogle(refresh_rotations={"SYNTHETIC_REFRESH_BEFORE": "SYNTHETIC_REFRESH_AFTER"})
    monkeypatch.setenv("FIRRA_GOOGLE_CLIENT_ID", "desktop-test")
    monkeypatch.setenv("FIRRA_GOOGLE_CLIENT_SECRET", "SYNTHETIC_CLIENT_SECRET")

    class FakeRuntime(LocalRuntime):
        def __init__(
            self,
            settings: Settings,
            secrets: SecretStore,
            *,
            identity: LocalIdentity | None = None,
            control: ControlServer | None = None,
        ) -> None:
            super().__init__(settings, secrets, identity=identity, control=control)
            self.http.close()
            self.http = fake.client()
            google = self.context.google
            assert isinstance(google, LocalGoogleTokens)
            google._client = self.http
            self.context = dataclasses.replace(
                self.context, http=self.http, time_budget_seconds=0.001
            )
            self.executor.context = self.context
            self.scheduler.context = self.context

    monkeypatch.setattr("firra.local.engine.LocalRuntime", FakeRuntime)
    with tempfile.TemporaryDirectory(prefix="firra-google-", dir="/tmp") as root:
        paths = Paths.under(Path(root) / "home", Path(PG_BIN), Path(root) / "run")
        engine = Engine(paths, version="test", expected_app_pid=os.getpid())
        thread = threading.Thread(target=engine.run, daemon=True)
        thread.start()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(30)
        try:
            wait_for(paths.control_socket.exists)
            sock.connect(str(paths.control_socket))
            reader = sock.makefile("rb")

            def send(**message: Any) -> None:
                sock.sendall((json.dumps(message) + "\n").encode())

            send(type="hello", protocol=1, app_version="test")
            assert json.loads(reader.readline())["type"] == "hello_ack"
            send(
                type="secret_set",
                name="db_encryption_key",
                value="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAE=",
            )
            send(type="secrets_complete")
            ready = json.loads(reader.readline())
            assert ready["type"] == "ready"
            runtime = engine._runtime
            assert runtime is not None
            ws = engine.identity.workspace_id
            with runtime.pool.connection() as db:
                client = db.execute(
                    "insert into public.client (workspace_id,name) "
                    "values (%s,'Synthetic') returning id",
                    (ws,),
                ).fetchone()
                assert client is not None
                db.execute(
                    "insert into public.client_domain (workspace_id,client_id,domain) "
                    "values (%s,%s,'acme.com')",
                    (ws, client["id"]),
                )
            now = datetime.now(UTC)
            for index in range(5):
                fake.add(
                    f"mail-{index}",
                    email_bytes(
                        sender="ceo@acme.com",
                        to=["owner@firra.local"],
                        date=format_datetime(now - timedelta(days=index + 1)),
                        message_id=f"<mail-{index}@acme.com>",
                    ),
                )
            fake.put_event(
                event(
                    "meeting",
                    (now - timedelta(days=2)).isoformat(),
                    (now - timedelta(days=2) + timedelta(hours=1)).isoformat(),
                    ["owner@firra.local", "ceo@acme.com"],
                )
            )
            root_id = uuid4()
            slot = f"google_refresh_token:{root_id}"
            send(type="secret_set", name=slot, value="SYNTHETIC_REFRESH_BEFORE")
            send(type="ping")
            assert json.loads(reader.readline())["type"] == "pong"
            with httpx.Client(
                base_url=f"http://127.0.0.1:{ready['api_port']}",
                headers={"Authorization": f"Bearer {ready['api_token']}"},
                trust_env=False,
            ) as api:
                body = {
                    "connection_id": str(root_id),
                    "account_label": "owner@firra.local",
                    "backfill_months": 12,
                }
                response = api.post("/local/connections/google", json=body)
                assert response.status_code == 200, response.text
                ids = response.json()
                gmail = ids["gmail_connection_id"]
                calendar = ids["calendar_connection_id"]
                rotated = json.loads(reader.readline())
                assert rotated == {
                    "type": "secret_set",
                    "name": slot,
                    "value": "SYNTHETIC_REFRESH_AFTER",
                }

                def all_finished() -> bool:
                    with runtime.pool.connection() as db:
                        row = db.execute(
                            "select count(*) as n from public.job "
                            "where workspace_id=%s and status in ('queued','running')",
                            (ws,),
                        ).fetchone()
                        assert row is not None
                        return bool(row["n"] == 0)

                wait_for(all_finished)
                assert api.post("/local/connections/google", json=body).json() == ids
                wait_for(all_finished)
                with runtime.pool.connection() as db:
                    rows = db.execute(
                        "select kind, body_clean from public.source_record where workspace_id=%s",
                        (ws,),
                    ).fetchall()
                    assert len(rows) == 6
                    assert all(
                        row["body_clean"].startswith("enc:v1:")
                        for row in rows
                        if row["kind"] == "email"
                    )
                    count = db.execute(
                        "select count(*) as n from public.job where workspace_id=%s", (ws,)
                    ).fetchone()
                    assert count is not None and count["n"] == 26
                    db.execute(
                        "update public.connection set cursor = cursor || "
                        "%s::jsonb where workspace_id=%s and id=%s",
                        (json.dumps({"history_id": "1"}), ws, gmail),
                    )
                assert api.post(f"/local/connections/{gmail}/sync").status_code == 202
                wait_for(all_finished)
                with runtime.pool.connection() as db:
                    recovered = db.execute(
                        "select progress, status::text from public.job "
                        "where workspace_id=%s and type='sync.incremental' "
                        "and payload->>'connection_id'=%s order by created_at desc limit 1",
                        (ws, gmail),
                    ).fetchone()
                    assert recovered is not None and recovered["status"] == "succeeded"
                    assert recovered["progress"]["history_reset"] is True
                fake.expired_sync_tokens.add(str(len(fake.event_log)))
                assert api.post(f"/local/connections/{calendar}/sync").status_code == 202
                wait_for(all_finished)
                with runtime.pool.connection() as db:
                    rows = db.execute(
                        "select id, cursor from public.connection where workspace_id=%s", (ws,)
                    ).fetchall()
                    assert all(row["cursor"].get("gmail_resync") is None for row in rows)
                    assert (
                        cast(
                            DictRow,
                            db.execute(
                                "select count(*) as n from public.source_record "
                                "where workspace_id=%s",
                                (ws,),
                            ).fetchone(),
                        )["n"]
                        == 6
                    )
                with runtime.pool.connection() as db:
                    recovered = db.execute(
                        "select progress, status::text from public.job "
                        "where workspace_id=%s and type='sync.incremental' "
                        "and payload->>'connection_id'=%s order by created_at desc limit 1",
                        (ws, calendar),
                    ).fetchone()
                    assert recovered is not None and recovered["status"] == "succeeded"
                    assert recovered["progress"]["sync_reset"] is True
                fake.expired_sync_tokens.clear()
                fake.add(
                    "wake-mail",
                    email_bytes(
                        sender="ceo@acme.com",
                        to=["owner@firra.local"],
                        date=format_datetime(now),
                        message_id="<wake@acme.com>",
                    ),
                    record_history=True,
                )
                assert api.post(f"/local/connections/{gmail}/sync").status_code == 202
                wait_for(all_finished)
                with runtime.pool.connection() as db:
                    assert (
                        cast(
                            DictRow,
                            db.execute(
                                "select count(*) as n from public.source_record "
                                "where workspace_id=%s",
                                (ws,),
                            ).fetchone(),
                        )["n"]
                        == 7
                    )
                    assert (
                        cast(
                            DictRow,
                            db.execute(
                                "select count(*) as n from public.connection where workspace_id=%s "
                                "and token_ciphertext is not null",
                                (ws,),
                            ).fetchone(),
                        )["n"]
                        == 0
                    )
                google = runtime.context.google
                assert isinstance(google, LocalGoogleTokens)
                google._cache.clear()
                fake.revoked_refresh_tokens.add("SYNTHETIC_REFRESH_AFTER")
                assert api.post(f"/local/connections/{gmail}/sync").status_code == 202
                refused = json.loads(reader.readline())
                assert refused == {"type": "event", "event": "need_reauth", "connection_id": gmail}
                wait_for(all_finished)
                with runtime.pool.connection() as db:
                    assert (
                        cast(
                            DictRow,
                            db.execute(
                                "select status::text from public.connection where workspace_id=%s "
                                "and id=%s",
                                (ws, gmail),
                            ).fetchone(),
                        )["status"]
                        == "revoked"
                    )
                assert api.delete(f"/local/connections/{calendar}").status_code == 204
                assert json.loads(reader.readline()) == {"type": "secret_delete", "name": slot}
                assert engine.secrets.get(slot) is None
                assert api.post(f"/local/connections/{calendar}/sync").status_code == 409
                assert api.post(f"/local/connections/{uuid4()}/sync").status_code == 404
            assert not fake.watch_topics
            dump = subprocess.run(  # noqa: S603 - bundled executable, synthetic cluster
                [str(Path(PG_BIN) / "pg_dump"), "--data-only", engine._pg.conninfo],
                capture_output=True,
                check=True,
                timeout=20,
            ).stdout
            forbidden = [
                b"SYNTHETIC_REFRESH_BEFORE",
                b"SYNTHETIC_REFRESH_AFTER",
                b"SYNTHETIC_CLIENT_SECRET",
                b"access-ok",
            ]
            assert all(value not in dump for value in forbidden)
            send(type="shutdown")
            thread.join(timeout=30)
            assert not thread.is_alive()
            logs = capsys.readouterr()
            assert all(value not in (logs.out + logs.err).encode() for value in forbidden)
            for path in Path(root).rglob("*"):
                if path.is_file():
                    assert all(value not in path.read_bytes() for value in forbidden)
        finally:
            engine.stop()
            sock.close()
            thread.join(timeout=30)
