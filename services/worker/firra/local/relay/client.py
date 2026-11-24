"""The engine's outbound device link to the relay (docs/plans/local/relay.md sections 4 and 8).

One WebSocket to ``<relay>/device``, opened by this Mac. The relay forwards MCP calls that carry a
connector-link secret; this client checks the secret against its own links again, sets the link's
scopes and hands the call to the local MCP endpoint. Logs hold counts and error classes only.
"""

import asyncio
import base64
import contextlib
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from firra.local.relay import links
from firra.local.settings import load
from firra.logging import get_logger

log = get_logger(__name__)
FRAME_LIMIT = 1024 * 1024
CHUNK_BYTES = 512 * 1024
BACKOFF = (1, 2, 5, 10, 30, 60)


def load_key(seed_b64: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed_b64, validate=True))


def new_seed() -> str:
    raw = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    return base64.b64encode(raw).decode()


def public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


class RelayClient:
    def __init__(
        self,
        home: Path,
        get_key: Callable[[], Ed25519PrivateKey],
        local_api: Callable[[], tuple[int, str]],
    ) -> None:
        self.home = home
        self._get_key = get_key
        self._local_api = local_api
        self.state = "not_registered"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False

    @property
    def key(self) -> Ed25519PrivateKey:
        """Made on first use, so an engine that never uses links never asks the app to store one."""
        return self._get_key()

    @property
    def device_id(self) -> str:
        return links.device_id(public_bytes(self.key))

    # -- thread-safe controls ------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="relay-client", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        self.notify()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def notify(self) -> None:
        """Settings changed (relay address or links): resync grants or reconnect."""
        if self._loop is not None and self._wake is not None:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._wake.set)

    # -- loop ------------------------------------------------------------------------------------

    def _run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        attempt = 0
        while not self._stopping:
            url = load(self.home).relay_url
            if not url:
                self.state = "not_registered"
                await self._sleep(30)
                continue
            self.state = "connecting"
            try:
                await self._session(url)
                attempt = 0
            except (OSError, websockets.WebSocketException, ValueError) as exc:
                log.info("relay.disconnected", error_class=type(exc).__name__)
            self.state = "offline"
            await self._sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
            attempt += 1

    async def _sleep(self, seconds: float) -> None:
        assert self._wake is not None  # noqa: S101 - set in _main
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), seconds)
        self._wake.clear()

    async def _session(self, url: str) -> None:
        assert self._wake is not None  # noqa: S101
        # Settings only accept https; plain http is for the loopback tests.
        endpoint = url.replace("http", "ws", 1) + "/device"
        async with websockets.connect(endpoint, max_size=FRAME_LIMIT, open_timeout=15) as ws:
            challenge = json.loads(await ws.recv())
            if challenge.get("type") != "challenge":
                raise ValueError("no challenge")
            await ws.send(json.dumps(self._hello(challenge["nonce"])))
            if json.loads(await ws.recv()).get("type") != "hello_ok":
                raise ValueError("hello refused")
            self.state = "online"
            log.info("relay.online")
            sent: set[str] = set()
            tasks: dict[str, asyncio.Task[None]] = {}
            await self._sync_grants(ws, sent)
            reader = asyncio.ensure_future(ws.recv())
            waker = asyncio.ensure_future(self._wake.wait())
            try:
                while not self._stopping:
                    done, _ = await asyncio.wait(
                        {reader, waker}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if waker in done:
                        self._wake.clear()
                        if load(self.home).relay_url != url:
                            return
                        await self._sync_grants(ws, sent)
                        waker = asyncio.ensure_future(self._wake.wait())
                    if reader in done:
                        frame = json.loads(reader.result())
                        reader = asyncio.ensure_future(ws.recv())
                        if frame.get("type") == "error" and frame.get("code") == "replaced":
                            self.state = "offline"
                            await asyncio.sleep(30)
                            return
                        await self._handle(ws, frame, tasks)
            finally:
                for pending in (reader, waker, *tasks.values()):
                    pending.cancel()

    def _hello(self, nonce: str) -> dict[str, Any]:
        public = public_bytes(self.key)
        return {
            "type": "hello",
            "protocol": 1,
            "device_id": self.device_id,
            "public_key": base64.b64encode(public).decode(),
            "signature": base64.b64encode(self.key.sign(base64.b64decode(nonce))).decode(),
        }

    async def _sync_grants(self, ws: Any, sent: set[str]) -> None:
        current = {link.id: link.hash for link in load(self.home).relay_links}
        for link_id in sent - set(current):
            await ws.send(json.dumps({"type": "grant_remove", "id": link_id}))
        for link_id, digest in current.items():
            if link_id not in sent:
                await ws.send(json.dumps({"type": "grant_add", "id": link_id, "hash": digest}))
        sent.clear()
        sent.update(current)

    async def _handle(self, ws: Any, frame: dict[str, Any], tasks: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "ping":
            await ws.send(json.dumps({"type": "pong"}))
        elif kind == "request":
            rid = frame["id"]
            tasks[rid] = asyncio.ensure_future(self._answer(ws, frame, tasks))
        elif kind == "cancel" and (task := tasks.pop(str(frame.get("id")), None)) is not None:
            task.cancel()

    async def _answer(self, ws: Any, frame: dict[str, Any], tasks: dict[str, Any]) -> None:
        rid = frame["id"]
        try:
            status, headers, body = await self._call_local(frame)
        except (httpx.HTTPError, ValueError):
            status, headers, body = 502, {}, b""
        finally:
            tasks.pop(rid, None)
        parts = [body[i : i + CHUNK_BYTES] for i in range(0, len(body), CHUNK_BYTES)] or [b""]
        first = {
            "type": "response",
            "id": rid,
            "status": status,
            "headers": headers,
            "body_b64": base64.b64encode(parts[0]).decode(),
            "final": len(parts) == 1,
        }
        await ws.send(json.dumps(first))
        for seq, part in enumerate(parts[1:], start=1):
            chunk = {
                "type": "chunk",
                "id": rid,
                "seq": seq,
                "body_b64": base64.b64encode(part).decode(),
                "final": seq == len(parts) - 1,
            }
            await ws.send(json.dumps(chunk))

    async def _call_local(self, frame: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        auth = frame["headers"].get("authorization", "")
        link = (
            links.find(self.home, auth.removeprefix("Bearer "))
            if auth.startswith("Bearer ")
            else None
        )
        if link is None:
            return 401, {}, b""
        port, token = self._local_api()
        headers = {
            k: v
            for k, v in frame["headers"].items()
            if k in ("accept", "content-type", "mcp-protocol-version")
        }
        headers["authorization"] = f"Bearer {token}"
        headers["x-firra-scopes"] = ",".join(link.scopes)
        async with httpx.AsyncClient(timeout=frame["deadline_ms"] / 1000) as client:
            response = await client.post(
                f"http://127.0.0.1:{port}/mcp",
                content=base64.b64decode(frame["body_b64"]),
                headers=headers,
            )
        out = {k: v for k, v in response.headers.items() if k in ("content-type", "mcp-session-id")}
        return response.status_code, out, response.content
