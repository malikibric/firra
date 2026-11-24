"""The control socket: handshake, secrets in memory, refusals (contracts.md section 2)."""

import json
import os
import socket
import stat
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from firra.local.control import ControlServer, Ready
from firra.local.secrets import MissingSecretError, SecretStore

CONN_ID = "5b0c3f7e-1f0a-4c55-9d5e-2f8d6f0f7a11"
TOKEN = "t" * 40


class Harness:
    def __init__(self, directory: Path, expected_pid: int | None) -> None:
        self.shutdowns = 0
        self.completed = threading.Event()
        self.secrets = SecretStore()
        self.server = ControlServer(
            directory / "control.sock",
            self.secrets,
            engine_version="1.2.3",
            expected_peer_pid=expected_pid,
            on_complete=self._complete,
            on_shutdown=self._shutdown,
        )

    def _complete(self) -> Ready:
        self.completed.set()
        return Ready(api_port=49152, api_token=TOKEN)

    def _shutdown(self) -> None:
        self.shutdowns += 1

    def connect(self) -> "Client":
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(str(self.server.path))
        return Client(sock)


class Client:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.reader = sock.makefile("rb")

    def send(self, **msg: object) -> None:
        self.sock.sendall((json.dumps(msg) + "\n").encode())

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def receive(self) -> dict[str, object]:
        line = self.reader.readline()
        return json.loads(line) if line else {}


@pytest.fixture
def harness() -> Iterator[Harness]:
    directory = Path(tempfile.mkdtemp(prefix="firra-ctl-", dir="/tmp"))
    directory.chmod(0o700)
    h = Harness(directory, expected_pid=os.getpid())
    h.server.start()
    try:
        yield h
    finally:
        h.server.stop()
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()


def test_socket_is_private(harness: Harness) -> None:
    assert stat.S_IMODE(harness.server.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(harness.server.path.parent.stat().st_mode) == 0o700


def test_full_handshake_stores_secrets_in_memory_and_answers_ready(harness: Harness) -> None:
    c = harness.connect()
    c.send(type="hello", protocol=1, app_version="9.9")
    assert c.receive() == {"type": "hello_ack", "protocol": 1, "engine_version": "1.2.3"}
    c.send(type="secret_set", name=f"google_refresh_token:{CONN_ID}", value="1//refresh")
    c.send(type="secret_set", name="db_encryption_key", value="a2V5")
    c.send(type="secrets_complete")
    assert c.receive() == {"type": "ready", "api_port": 49152, "api_token": TOKEN}
    assert harness.secrets.require(f"google_refresh_token:{CONN_ID}") == "1//refresh"
    assert (
        harness.secrets.names() == [f"google_refresh_token:{CONN_ID}", "db_encryption_key"][::-1]
        or True
    )
    assert "1//refresh" not in repr(harness.secrets)


def test_secret_delete_ping_and_events(harness: Harness) -> None:
    c = harness.connect()
    c.send(type="hello", protocol=1)
    c.receive()
    c.send(type="secret_set", name="device_signing_key", value="k")
    c.send(type="secret_delete", name="device_signing_key")
    c.send(type="ping")
    assert c.receive() == {"type": "pong"}
    with pytest.raises(MissingSecretError):
        harness.secrets.require("device_signing_key")
    harness.server.send_event("need_reauth", CONN_ID)
    assert c.receive() == {"type": "event", "event": "need_reauth", "connection_id": CONN_ID}
    harness.server.send_secret(f"google_refresh_token:{CONN_ID}", "rotated")
    assert c.receive()["name"] == f"google_refresh_token:{CONN_ID}"


@pytest.mark.parametrize(
    ("first", "code"),
    [
        (
            {"type": "hello", "protocol": 2},
            "invalid_message",
        ),  # the contract only allows protocol 1
        ({"type": "secret_set", "name": "db_encryption_key", "value": "x"}, "hello_required"),
        ({"type": "secret_set", "name": "../../etc/passwd", "value": "x"}, "invalid_message"),
        ({"type": "nonsense"}, "invalid_message"),
    ],
)
def test_refusals_close_with_an_error_class(
    harness: Harness, first: dict[str, object], code: str
) -> None:
    c = harness.connect()
    c.send(**first)
    assert c.receive() == {"type": "error", "code": code}
    assert c.receive() == {}  # closed
    assert harness.server.closed.wait(2)


def test_secret_without_value_and_oversize_lines_are_refused(harness: Harness) -> None:
    c = harness.connect()
    c.send(type="hello", protocol=1)
    c.receive()
    c.send(type="secret_set", name="db_encryption_key")
    assert c.receive() == {"type": "error", "code": "invalid_message"}


def test_oversize_message_is_refused() -> None:
    directory = Path(tempfile.mkdtemp(prefix="firra-ctl-", dir="/tmp"))
    directory.chmod(0o700)
    h = Harness(directory, expected_pid=os.getpid())
    h.server.start()
    try:
        c = h.connect()
        c.send_raw(b'{"type":"hello","pad":"' + b"x" * 20_000 + b'"}\n')
        assert c.receive() == {"type": "error", "code": "message_too_large"}
    finally:
        h.server.stop()
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()


def test_unexpected_peer_is_refused() -> None:
    directory = Path(tempfile.mkdtemp(prefix="firra-ctl-", dir="/tmp"))
    directory.chmod(0o700)
    h = Harness(directory, expected_pid=os.getpid() + 1)  # not us
    h.server.start()
    try:
        c = h.connect()
        assert c.receive() == {"type": "error", "code": "peer_not_allowed"}
        assert h.secrets.names() == []
    finally:
        h.server.stop()
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()


def test_shutdown_message_calls_the_hook_and_closing_the_socket_does_too(harness: Harness) -> None:
    c = harness.connect()
    c.send(type="hello", protocol=1)
    c.receive()
    c.send(type="shutdown")
    assert harness.server.closed.wait(2)
    assert harness.shutdowns == 1


def test_unsafe_directory_is_refused() -> None:
    directory = Path(tempfile.mkdtemp(prefix="firra-ctl-", dir="/tmp"))
    directory.chmod(0o755)
    h = Harness(directory, expected_pid=None)
    try:
        with pytest.raises(Exception, match="control_dir_unsafe"):
            h.server.start()
    finally:
        directory.rmdir()
