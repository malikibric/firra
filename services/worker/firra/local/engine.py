"""The engine process: control socket, private Postgres, local API (docs/plans/local/contracts.md).

``Engine.run()`` blocks until the app says ``shutdown`` (or goes away). Order: open the control
socket, wait for the app's secrets, start Postgres and migrate, start the loopback API, answer
``ready``. Later work packages hang their services off ``_start_services`` (job executor,
scheduler, connectors, relay client) and ``_stop_services``.
"""

import contextlib
import json
import os
import secrets
import socket
import threading
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from firra.db.local_identity import LocalIdentity
from firra.local.bootstrap import ensure_local_owner
from firra.local.control import ControlServer, Ready
from firra.local.pg import PostgresSupervisor
from firra.local.profile import LocalRuntime, build_settings
from firra.local.readctx import ReadContext
from firra.local.relay.client import RelayClient, load_key, new_seed
from firra.local.routes import all_routers
from firra.local.routes.connections import build_router as connections_router
from firra.local.routes.relay_links import build_router as relay_links_router
from firra.local.routes.slack import build_router as slack_router
from firra.local.secrets import SecretStore
from firra.local.server import create_local_app
from firra.local.wipe import Wipe
from firra.logging import get_logger
from firra_contracts import EngineStatus

log = get_logger(__name__)


@dataclass(frozen=True)
class Paths:
    """Everything the engine writes lives under ``home`` (0700)."""

    home: Path
    pg_bin: Path
    run: Path

    @classmethod
    def under(cls, home: Path, pg_bin: Path, run: Path | None = None) -> "Paths":
        return cls(home=home, pg_bin=pg_bin, run=run or home / "run")

    @property
    def data(self) -> Path:
        return self.home / "pg"

    @property
    def control_socket(self) -> Path:
        return self.run / "control.sock"

    @property
    def engine_json(self) -> Path:
        return self.run / "engine.json"


def _dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


class Engine:
    def __init__(
        self,
        paths: Paths,
        *,
        version: str,
        expected_app_pid: int | None,
        wait_for_app_seconds: float | None = None,
    ) -> None:
        self.paths = paths
        self.version = version
        self.secrets = SecretStore()
        self._stop = threading.Event()
        self._pg = PostgresSupervisor(paths.pg_bin, paths.data, paths.run)
        self._database = "starting"
        self._runtime: LocalRuntime | None = None
        self._ready: Ready | None = None
        self._relay: RelayClient | None = None
        self._key_lock = threading.Lock()
        self._api: tuple[int, str] = (0, "")
        self._http: uvicorn.Server | None = None
        self._http_thread: threading.Thread | None = None
        self._control = ControlServer(
            paths.control_socket,
            self.secrets,
            engine_version=version,
            expected_peer_pid=expected_app_pid,
            on_complete=self._start_services,
            on_shutdown=self._stop.set,
        )

    # -- lifecycle -------------------------------------------------------------------------------

    def run(self) -> int:
        self.paths.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._control.start()
        log.info("engine.waiting_for_app")
        try:
            self._stop.wait()
        finally:
            self._stop_services()
            self._control.stop()
        return 0

    def stop(self) -> None:
        self._stop.set()

    def _start_services(self) -> Ready:
        if self._ready is not None:
            return self._ready
        self._database = "migrating"
        conninfo = self._pg.ensure_ready()
        self.identity: LocalIdentity = ensure_local_owner(conninfo)
        self._runtime = LocalRuntime(
            build_settings(conninfo, self.paths.home),
            self.secrets,
            identity=self.identity,
            control=self._control,
        )
        self._runtime.start()
        self._database = "ready"
        token = secrets.token_urlsafe(32)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
        # The runtime's pool is bound to the body key, so read models decrypt like the handlers.
        ctx = ReadContext(
            pool=self._runtime.pool,
            identity=self.identity,
            home=self.paths.home,
            engine_version=self.version,
        )
        self._api = (port, token)
        relay = self._relay = RelayClient(self.paths.home, self._device_key, lambda: self._api)
        app = create_local_app(
            api_token=token,
            port=port,
            status=self._status,
            routers=all_routers(
                ctx,
                processing_enqueue=self._runtime.executor.enqueue,
                relay_router=relay_links_router(
                    ctx,
                    device_id=lambda: relay.device_id,
                    state=lambda: relay.state,
                    changed=relay.notify,
                ),
                slack_router=slack_router(
                    ctx,
                    secrets=self.secrets,
                    tasks=self._runtime.context,
                    send_secret_delete=self._control.send_secret_delete,
                ),
                wipe=Wipe(
                    secrets=self.secrets,
                    send_secret_delete=self._control.send_secret_delete,
                    http=self._runtime.http,
                ),
                connection_router=connections_router(
                    ctx,
                    secrets=self.secrets,
                    tasks=self._runtime.context,
                    send_secret_delete=self._control.send_secret_delete,
                ),
            ),
        )
        config = uvicorn.Config(app, log_config=None, access_log=False, lifespan="off")
        self._http = uvicorn.Server(config)
        self._http_thread = threading.Thread(
            target=self._http.run, kwargs={"sockets": [listener]}, name="local-api", daemon=True
        )
        self._http_thread.start()
        relay.start()
        self._write_engine_json(port, token)
        log.info("engine.ready", api_port=port)
        self._ready = Ready(api_port=port, api_token=token)
        return self._ready

    def _device_key(self) -> Ed25519PrivateKey:
        """The device key lives in the Keychain; the first start makes one for the app to keep."""
        with self._key_lock:
            seed = self.secrets.get("device_signing_key")
            if seed is None:
                seed = new_seed()
                self.secrets.set("device_signing_key", seed)
                self._control.send_secret("device_signing_key", seed)
            return load_key(seed)

    def _stop_services(self) -> None:
        if self._relay is not None:
            self._relay.stop()
        if self._runtime is not None:
            with contextlib.suppress(Exception):
                self._runtime.stop()
        if self._http is not None:
            self._http.should_exit = True
        if self._http_thread is not None:
            self._http_thread.join(timeout=10)
        with contextlib.suppress(Exception):
            self._pg.stop()
        self.paths.engine_json.unlink(missing_ok=True)

    def _write_engine_json(self, port: int, token: str) -> None:
        body = {
            "port": port,
            "token": token,
            "pid": os.getpid(),
            "version": self.version,
        }
        target = self.paths.engine_json
        tmp = target.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(body, handle)
        tmp.replace(target)

    # -- status ----------------------------------------------------------------------------------

    def _status(self) -> EngineStatus:
        return EngineStatus.model_validate(
            {
                "engine_version": self.version,
                "database": self._database,
                "connections": [],
                "ai": self._runtime.ai.status().model_dump(mode="json")
                if self._runtime is not None and self._runtime.ai is not None
                else {
                    "executor": "conversation_only",
                    "tools": [],
                    "queue": {"pending": 0, "done": 0, "rejected_items": 0},
                    "state": "conversation_only",
                },
                "relay": {"state": self._relay.state if self._relay else "not_registered"},
                "disk": {"data_bytes": _dir_bytes(self.paths.data)},
            }
        )
