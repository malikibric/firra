"""Test helpers: a real relay on a random local port (uvicorn in a thread) and a fake device."""

import asyncio
import base64
import hashlib
import json
import socket
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any
from uuid import uuid4

import uvicorn
import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from firra_relay.app import FRAME_LIMIT, create_app, server_config
from firra_relay.hub import Hub


class Relay:
    def __init__(self, hub: Hub, port: int) -> None:
        self.hub = hub
        self.http = f"http://127.0.0.1:{port}"
        self.ws = f"ws://127.0.0.1:{port}/device"


def start(deadline_ms: int) -> Iterator[Relay]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    hub = Hub()
    server = uvicorn.Server(
        server_config(
            create_app(hub, deadline_ms=deadline_ms), host="127.0.0.1", port=port, log_level="error"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield Relay(hub, port)
    finally:
        server.should_exit = True
        thread.join(timeout=5)


class Identity:
    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.device_id = hashlib.sha256(self.public).hexdigest()[:16]
        self.secret = base64.urlsafe_b64encode(hashlib.sha256(uuid4().bytes).digest())[:43].decode()
        self.grant = str(uuid4())
        self.hash = hashlib.sha256(self.secret.encode()).hexdigest()

    @property
    def token(self) -> str:
        return f"{self.device_id}.{self.secret}"

    def hello(self, nonce: str) -> dict[str, Any]:
        return {
            "type": "hello",
            "protocol": 1,
            "device_id": self.device_id,
            "public_key": base64.b64encode(self.public).decode(),
            "signature": base64.b64encode(self.key.sign(base64.b64decode(nonce))).decode(),
        }


async def connect(relay: Relay, identity: Identity, *, grant: bool = True) -> Any:
    ws = await websockets.connect(relay.ws, max_size=FRAME_LIMIT)
    challenge = json.loads(await ws.recv())
    if challenge["type"] != "challenge":
        raise RuntimeError("no challenge")
    await ws.send(json.dumps(identity.hello(challenge["nonce"])))
    if json.loads(await ws.recv())["type"] != "hello_ok":
        raise RuntimeError("no hello_ok")
    if grant:
        await ws.send(
            json.dumps({"type": "grant_add", "id": identity.grant, "hash": identity.hash})
        )
        await asyncio.sleep(0.1)
    return ws


Handler = Callable[[Any, dict[str, Any]], Awaitable[None]]


def run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)  # type: ignore[arg-type]
