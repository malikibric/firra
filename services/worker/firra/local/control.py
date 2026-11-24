"""The engine's side of the app <-> engine control socket (docs/plans/local/contracts.md section 2).

One connection at a time, from a process of the same user that the engine expects (the app that
spawned it). Messages are JSON lines matching ``HandshakeMessage``. Secrets arrive here and only
here; they are stored in memory (``SecretStore``). Nothing in this module logs a message body: log
lines carry message types and error classes only.
"""

import contextlib
import json
import os
import platform
import socket
import stat
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from firra.local.secrets import SecretStore
from firra.logging import get_logger
from firra_contracts import HandshakeMessage

log = get_logger(__name__)

PROTOCOL = 1
MAX_LINE_BYTES = 16 * 1024
ACCEPT_TIMEOUT_SECONDS = 30.0
_LOCAL_PEERPID = 0x002  # macOS: getsockopt(SOL_LOCAL, LOCAL_PEERPID)
_SOL_LOCAL = 0


class ControlError(Exception):
    """A protocol violation; ``code`` is a class name that is also sent to the app."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Ready:
    api_port: int
    api_token: str


def peer_pid(conn: socket.socket) -> int | None:
    """The process id on the other end of a unix socket, or None where the platform cannot say."""
    system = platform.system()
    try:
        if system == "Darwin":
            return int(conn.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID))
        if system == "Linux":
            size = struct.calcsize("3i")
            return int(
                struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, 17, size))[0]
            )  # SO_PEERCRED
    except OSError:
        return None
    return None


class ControlServer:
    """Accepts the app, receives its secrets, answers ``ready`` and then serves pings and events."""

    def __init__(
        self,
        path: Path,
        secrets: SecretStore,
        *,
        engine_version: str,
        expected_peer_pid: int | None,
        on_complete: Callable[[], Ready],
        on_shutdown: Callable[[], None],
    ) -> None:
        self.path = path
        self.secrets = secrets
        self._engine_version = engine_version
        self._expected_pid = expected_peer_pid
        self._on_complete = on_complete
        self._on_shutdown = on_shutdown
        self._listener: socket.socket | None = None
        self._conn: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.closed = threading.Event()

    # -- lifecycle -----------------------------------------------------------------------------

    def start(self) -> None:
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(parent.stat().st_mode) & 0o077 or parent.stat().st_uid != os.getuid():
            raise ControlError("control_dir_unsafe")
        self.path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)
        try:
            listener.bind(str(self.path))
        finally:
            os.umask(old_umask)
        self.path.chmod(0o600)
        listener.listen(1)
        listener.settimeout(ACCEPT_TIMEOUT_SECONDS)
        self._listener = listener
        self._thread = threading.Thread(target=self._serve, name="control-socket", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        for sock in (self._conn, self._listener):
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.close()
        self.path.unlink(missing_ok=True)
        self.closed.set()

    # -- messages to the app ---------------------------------------------------------------------

    def send_event(self, event: str, connection_id: str | None = None) -> None:
        msg: dict[str, object] = {"type": "event", "event": event}
        if connection_id is not None:
            msg["connection_id"] = connection_id
        self._send(msg)

    def send_secret(self, name: str, value: str) -> None:
        """Engine to app: a rotated token the app must store in the Keychain."""
        self._send({"type": "secret_set", "name": name, "value": value})

    def send_secret_delete(self, name: str) -> None:
        """Engine to app: remove a disconnected account from the Keychain."""
        self._send({"type": "secret_delete", "name": name})

    def _send(self, msg: dict[str, object]) -> None:
        conn = self._conn
        if conn is None:
            return
        line = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        with self._send_lock:
            try:
                conn.sendall(line)
            except OSError:
                log.warning("control.send_failed", message_type=str(msg.get("type")))

    # -- serving -----------------------------------------------------------------------------------

    def _serve(self) -> None:
        listener = self._listener
        if listener is None:
            return
        try:
            conn, _ = listener.accept()
        except (TimeoutError, OSError):
            log.error("control.no_app", error_class="accept_timeout")
            self.closed.set()
            return
        self._conn = conn
        try:
            self._check_peer(conn)
            self._loop(conn)
        except ControlError as exc:
            self._send({"type": "error", "code": exc.code})
            log.error("control.refused", error_class=exc.code)
        except OSError:
            log.info("control.closed")
        finally:
            with contextlib.suppress(OSError):
                conn.close()
            self.closed.set()
            self._on_shutdown()

    def _check_peer(self, conn: socket.socket) -> None:
        pid = peer_pid(conn)
        if self._expected_pid is not None and pid != self._expected_pid:
            raise ControlError("peer_not_allowed")

    def _loop(self, conn: socket.socket) -> None:
        reader = conn.makefile("rb")
        greeted = False
        while True:
            raw = reader.readline(MAX_LINE_BYTES + 1)
            if not raw:
                return  # the app is gone
            if len(raw) > MAX_LINE_BYTES:
                raise ControlError("message_too_large")
            msg = self._parse(raw)
            greeted = self._handle(msg, greeted)
            if msg.type.value == "shutdown":
                return

    @staticmethod
    def _parse(raw: bytes) -> HandshakeMessage:
        try:
            return HandshakeMessage.model_validate_json(raw)
        except ValidationError:
            raise ControlError("invalid_message") from None

    def _handle(self, msg: HandshakeMessage, greeted: bool) -> bool:
        kind = msg.type.value
        if kind == "hello":
            if msg.protocol != PROTOCOL:
                raise ControlError("protocol_mismatch")
            self._send(
                {"type": "hello_ack", "protocol": PROTOCOL, "engine_version": self._engine_version}
            )
            return True
        if not greeted:
            raise ControlError("hello_required")
        if kind == "secret_set":
            if msg.name is None or msg.value is None:
                raise ControlError("invalid_message")
            self.secrets.set(msg.name, msg.value)
            log.info("control.secret_set", slot=msg.name.split(":", 1)[0])
        elif kind == "secret_delete":
            if msg.name is None:
                raise ControlError("invalid_message")
            self.secrets.delete(msg.name)
            log.info("control.secret_delete", slot=msg.name.split(":", 1)[0])
        elif kind == "secrets_complete":
            ready = self._on_complete()
            self._send({"type": "ready", "api_port": ready.api_port, "api_token": ready.api_token})
            self.ready.set()
        elif kind == "ping":
            self._send({"type": "pong"})
        elif kind == "shutdown":
            pass
        else:
            raise ControlError("unexpected_message")
        return True
