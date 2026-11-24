"""The whole foundation together, with this test playing the app (plan L1-06, gate G-B).

Needs the bundled Postgres binaries (``FIRRA_LOCAL_PG_BIN``); skipped otherwise.
"""

import json
import os
import socket
import stat
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from firra.local.engine import Engine, Paths

pytestmark = pytest.mark.integration

PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")


def _line(sock: socket.socket) -> dict[str, object]:
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return json.loads(data) if data else {}


def test_app_handshake_status_and_clean_shutdown() -> None:
    if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    root = Path(tempfile.mkdtemp(prefix="firra-eng-", dir="/tmp"))
    paths = Paths.under(root / "home", Path(PG_BIN), root / "run")
    engine = Engine(paths, version="9.9.9", expected_app_pid=os.getpid())
    thread = threading.Thread(target=engine.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if paths.control_socket.exists():
                break
            threading.Event().wait(0.05)
        app = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        app.settimeout(60)
        app.connect(str(paths.control_socket))

        def send(**msg: object) -> None:
            app.sendall((json.dumps(msg) + "\n").encode())

        send(type="hello", protocol=1, app_version="1")
        assert _line(app)["type"] == "hello_ack"
        send(
            type="secret_set",
            name="db_encryption_key",
            value="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAE=",
        )
        send(type="secrets_complete")
        ready = _line(app)
        assert ready["type"] == "ready"
        port, token = int(str(ready["api_port"])), str(ready["api_token"])

        base = f"http://127.0.0.1:{port}"
        assert httpx.get(f"{base}/local/status").status_code == 401  # no token
        status = httpx.get(f"{base}/local/status", headers={"Authorization": f"Bearer {token}"})
        assert status.status_code == 200
        body = status.json()
        assert body["database"] == "ready"
        assert body["engine_version"] == "9.9.9"
        assert body["disk"]["data_bytes"] > 0

        auth = {"Authorization": f"Bearer {token}"}
        ws = engine.identity.workspace_id
        clients = httpx.get(f"{base}/v1/workspaces/{ws}/clients", headers=auth)
        assert clients.status_code == 200 and clients.json()["items"] == []
        other = httpx.get(f"{base}/v1/workspaces/{uuid4()}/clients", headers=auth)
        assert other.status_code == 403

        info = paths.engine_json
        assert stat.S_IMODE(info.stat().st_mode) == 0o600
        assert json.loads(info.read_text())["token"] == token
        assert (
            engine.secrets.require("db_encryption_key")
            == "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAE="
        )

        send(type="ping")
        assert _line(app)["type"] == "pong"
        send(type="shutdown")
        thread.join(timeout=60)
        assert not thread.is_alive()
        assert not info.exists()
        assert not paths.control_socket.exists()
        assert not engine._pg.is_running()
        with pytest.raises(httpx.ConnectError):
            httpx.get(f"{base}/local/status", headers={"Authorization": f"Bearer {token}"})
    finally:
        engine.stop()
        thread.join(timeout=30)
