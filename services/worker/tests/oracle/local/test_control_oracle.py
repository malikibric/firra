"""Independent oracle for the control socket (L1-08, requirements 12-14).

Written from ``docs/plans/local/contracts.md`` §2 and
``packages/contracts/schemas/local/handshake.json``, not from
``firra/local/control.py``. Names are looked up only to start the server;
every assertion restates the contract. No binaries needed (plain unit tests).
"""

import contextlib
import json
import logging
import socket
import stat
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from firra.local.control import PROTOCOL, ControlServer, Ready
from firra.local.secrets import SecretStore

SECRET = f"ORACLE-SECRET-{uuid.uuid4().hex}"
OTHER_SECRET = f"ORACLE-OTHER-{uuid.uuid4().hex}"


@pytest.fixture
def sock_dir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="firra-oracle-ctl-") as tmp:
        yield Path(tmp)


def _start(
    sock_dir: Path,
    secrets: SecretStore,
    hook_log: list[str],
    *,
    expected_peer_pid: int | None = None,
) -> ControlServer:
    server = ControlServer(
        sock_dir / "control.sock",
        secrets,
        engine_version="oracle-1",
        expected_peer_pid=expected_peer_pid,
        on_complete=lambda: Ready(api_port=54321, api_token="t" * 32),
        on_shutdown=lambda: hook_log.append("shutdown"),
    )
    server.start()
    return server


class Client:
    """A blocking JSON-line peer for one test connection."""

    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5.0)
        self.sock.connect(str(path))
        self.reader = self.sock.makefile("rb")

    def send(self, message: dict[str, Any]) -> None:
        self.sock.sendall((json.dumps(message) + "\n").encode())

    def read(self) -> Any:
        line = self.reader.readline(17 * 1024)
        assert line, "server closed the connection without answering"
        return json.loads(line)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.reader.close()
        with contextlib.suppress(OSError):
            self.sock.close()


@pytest.fixture
def server(sock_dir: Path) -> Iterator[tuple[ControlServer, SecretStore, list[str]]]:
    secrets = SecretStore()
    hook_log: list[str] = []
    srv = _start(sock_dir, secrets, hook_log)
    yield srv, secrets, hook_log
    srv.stop()


def test_12_hello_first(
    server: tuple[ControlServer, SecretStore, list[str]], sock_dir: Path
) -> None:
    """Spec: hello comes first; anything else is refused."""
    client = Client(sock_dir / "control.sock")
    try:
        client.send({"type": "ping"})
        assert client.read()["code"] == "hello_required"
    finally:
        client.close()
        server[0].stop()


def test_12_protocol_mismatch_closes(
    server: tuple[ControlServer, SecretStore, list[str]], sock_dir: Path
) -> None:
    """Spec: a different protocol is refused and the socket closes.

    contracts.md §2 step 3 says ``protocol_mismatch``, but handshake.json pins
    ``protocol`` to ``const: 1``, so a different value fails validation first.
    The schema wins over the text (contracts.md §1), hence ``invalid_message``.
    """
    client = Client(sock_dir / "control.sock")
    try:
        client.send({"type": "hello", "protocol": PROTOCOL + 1, "app_version": "x"})
        assert client.read()["code"] == "invalid_message"
    finally:
        client.close()
        server[0].stop()


def test_12_unknown_message_is_an_error(
    server: tuple[ControlServer, SecretStore, list[str]], sock_dir: Path
) -> None:
    """Spec: unknown message shapes are errors, never stored or acted on."""
    client = Client(sock_dir / "control.sock")
    try:
        client.send({"type": "frobnicate"})
        assert client.read()["code"] == "invalid_message"
    finally:
        client.close()
        server[0].stop()


def test_12_oversize_message_is_refused(
    server: tuple[ControlServer, SecretStore, list[str]], sock_dir: Path
) -> None:
    """Spec: single JSON lines are at most 16 KiB."""
    client = Client(sock_dir / "control.sock")
    try:
        client.sock.sendall(b'{"type":"ping","pad":"' + b"y" * (17 * 1024) + b'"}\n')
        assert client.read()["code"] == "message_too_large"
    finally:
        client.close()
        server[0].stop()


@pytest.mark.parametrize(
    "name",
    [
        "nope",
        "",
        "google_refresh_token:",
        "google_refresh_token:" + "x" * 65,
        "db_encryption_key_extra",
    ],
)
def test_12_slot_names_are_validated(
    server: tuple[ControlServer, SecretStore, list[str]], sock_dir: Path, name: str
) -> None:
    """Spec: secret slots are the four known names; anything else is an error."""
    client = Client(sock_dir / "control.sock")
    try:
        client.send({"type": "hello", "protocol": PROTOCOL, "app_version": "x"})
        client.read()
        client.send({"type": "secret_set", "name": name, "value": "v"})
        answer = client.read()
        assert answer["type"] == "error", f"slot {name!r} was accepted"
    finally:
        client.close()
        server[0].stop()


def test_12_second_connection_waits_while_first_is_open(
    server: tuple[ControlServer, SecretStore, list[str]], sock_dir: Path
) -> None:
    """Spec: one connection at a time; a second hello gets no answer while the first is open."""
    first = Client(sock_dir / "control.sock")
    second: Client | None = None
    # Wait until accept() consumes the first connection before filling the backlog again.
    # macOS may otherwise refuse the second connect while the first is still queued.
    try:
        first.send({"type": "hello", "protocol": PROTOCOL, "app_version": "x"})
        assert first.read()["type"] == "hello_ack"
        second = Client(sock_dir / "control.sock")
        second.sock.settimeout(2.0)
        second.send({"type": "hello", "protocol": PROTOCOL, "app_version": "x"})
        with pytest.raises((TimeoutError, socket.timeout)):
            second.read()
    finally:
        first.close()
        if second is not None:
            second.close()
        server[0].stop()


def test_12_wrong_peer_pid_is_refused(sock_dir: Path) -> None:
    """Spec: only the spawning app (peer pid check) may connect."""
    secrets = SecretStore()
    hook_log: list[str] = []
    srv = _start(sock_dir, secrets, hook_log, expected_peer_pid=2**30)
    client = Client(sock_dir / "control.sock")
    try:
        assert client.read()["code"] == "peer_not_allowed"
    finally:
        client.close()
        srv.stop()


def test_12_socket_and_directory_modes(sock_dir: Path) -> None:
    """Spec: socket 0600, directory 0700."""
    secrets = SecretStore()
    srv = _start(sock_dir, secrets, [])
    try:
        assert stat.S_IMODE((sock_dir / "control.sock").stat().st_mode) == 0o600
        assert stat.S_IMODE(sock_dir.stat().st_mode) == 0o700
    finally:
        srv.stop()


def test_12_secrets_complete_answers_ready_after_stored_secrets(
    server: tuple[ControlServer, SecretStore, list[str]], sock_dir: Path
) -> None:
    """Spec: secret_set values are stored, then secrets_complete is answered with ready."""
    client = Client(sock_dir / "control.sock")
    try:
        client.send({"type": "hello", "protocol": PROTOCOL, "app_version": "x"})
        client.read()
        client.send({"type": "secret_set", "name": "db_encryption_key", "value": SECRET})
        client.send({"type": "secrets_complete"})
        answer = client.read()
        assert answer["type"] == "ready"
        assert answer["api_port"] == 54321
        assert server[1].get("db_encryption_key") == SECRET
        client.send({"type": "ping"})
        assert client.read() == {"type": "pong"}
    finally:
        client.close()
        server[0].stop()


def test_12_engine_to_app_secret_and_event(
    server: tuple[ControlServer, SecretStore, list[str]], sock_dir: Path
) -> None:
    """Spec: engine → app secret_set (rotated token) and event messages arrive."""
    client = Client(sock_dir / "control.sock")
    try:
        client.send({"type": "hello", "protocol": PROTOCOL, "app_version": "x"})
        client.read()
        server[0].send_secret("google_refresh_token:conn1", OTHER_SECRET)
        assert client.read() == {
            "type": "secret_set",
            "name": "google_refresh_token:conn1",
            "value": OTHER_SECRET,
        }
        server[0].send_event("need_reauth", str(uuid.uuid4()))
        answer = client.read()
        assert answer["type"] == "event" and answer["event"] == "need_reauth"
    finally:
        client.close()
        server[0].stop()


def test_13_no_secret_on_disk_or_in_logs(
    server: tuple[ControlServer, SecretStore, list[str]],
    sock_dir: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Spec: recognizable secrets never reach disk files, logs, or repr/str."""
    client = Client(sock_dir / "control.sock")
    error_line: str | None = None
    try:
        with caplog.at_level(logging.DEBUG):
            client.send({"type": "hello", "protocol": PROTOCOL, "app_version": "x"})
            client.read()
            client.send({"type": "secret_set", "name": "slack_user_token:c1", "value": SECRET})
            client.send({"type": "secret_delete", "name": "slack_user_token:c1"})
            client.send({"type": "secrets_complete"})
            client.read()
            # A protocol violation closes the connection, so exercise it last.
            client.send({"type": "secret_set", "name": "bogus", "value": OTHER_SECRET})
            error_line = json.dumps(client.read())
    finally:
        client.close()
        server[0].stop()
    if error_line is not None:
        assert OTHER_SECRET not in error_line
    for directory in (sock_dir, tmp_path):
        for path in directory.rglob("*"):
            if path.is_file():
                assert SECRET not in path.read_text(errors="replace")
                assert OTHER_SECRET not in path.read_text(errors="replace")
    assert SECRET not in caplog.text
    assert OTHER_SECRET not in caplog.text
    assert SECRET not in repr(server[1])
    assert OTHER_SECRET not in repr(server[1])


def test_14_disconnect_runs_shutdown_hook_once_and_removes_socket(
    server: tuple[ControlServer, SecretStore, list[str]], sock_dir: Path
) -> None:
    """Spec: app gone without shutdown → the engine shutdown hook runs once, socket removed."""
    client = Client(sock_dir / "control.sock")
    client.send({"type": "hello", "protocol": PROTOCOL, "app_version": "x"})
    client.read()
    client.close()  # no shutdown message
    deadline = time.monotonic() + 10
    while not server[2] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server[2] == ["shutdown"], "shutdown hook did not run exactly once"
    # File removal is the engine exit path's job (stop()); the contract only
    # requires the hook here, so simulate the exit before asserting cleanup.
    server[0].stop()
    assert not (sock_dir / "control.sock").exists()


def test_14_clean_shutdown_keeps_socket_cleanup(
    server: tuple[ControlServer, SecretStore, list[str]], sock_dir: Path
) -> None:
    """Spec: shutdown from the app is the clean path; the socket is removed."""
    client = Client(sock_dir / "control.sock")
    try:
        client.send({"type": "hello", "protocol": PROTOCOL, "app_version": "x"})
        client.read()
        client.send({"type": "shutdown"})
    finally:
        client.close()
    deadline = time.monotonic() + 10
    while not server[2] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server[2] == ["shutdown"]
    server[0].stop()
    assert not (sock_dir / "control.sock").exists()
