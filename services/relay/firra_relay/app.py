"""The relay HTTP and WebSocket app (docs/plans/local/relay.md sections 1, 4 and 8)."""

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import re
import secrets
from collections.abc import Mapping
from typing import Any

import uvicorn
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from firra_contracts.generated.relay.relay_frame import (
    Hello,
    RelayFrame,
)
from firra_relay.hub import (
    MAX_DEVICES,
    MAX_IN_FLIGHT,
    MAX_RESPONSE_BYTES,
    Device,
    Hub,
    Pending,
)

log = logging.getLogger("firra.relay")
FRAME_LIMIT = 1024 * 1024
MAX_BODY = 512 * 1024
HELLO_SECONDS = 10
PING_SECONDS = 20
IDLE_SECONDS = 60
DEFAULT_DEADLINE_MS = 60_000
ALLOWED_HEADERS = ("accept", "content-type", "mcp-session-id", "mcp-protocol-version")
TOKEN = re.compile(r"^([0-9a-f]{16})\.([A-Za-z0-9_-]{43})$")
OFFLINE = "Firra on your Mac is offline"


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def client_ip(headers: Mapping[str, str], peer: str | None) -> str:
    """Cloud Run appends the real client address as the last X-Forwarded-For entry; the entries
    before it are whatever the caller sent, so only the last one is used for per-address limits."""
    last = headers.get("x-forwarded-for", "").rsplit(",", 1)[-1].strip()
    return last[:64] or peer or "unknown"


def server_config(app: FastAPI, *, host: str, port: int, log_level: str) -> uvicorn.Config:
    # proxy_headers is off on purpose: uvicorn with forwarded_allow_ips="*" would take the FIRST
    # (caller-supplied) X-Forwarded-For entry, which lets one client dodge every per-address limit.
    return uvicorn.Config(
        app,
        host=host,
        port=port,
        access_log=False,
        proxy_headers=False,
        ws_max_size=FRAME_LIMIT,
        log_level=log_level,
    )


def _request_id(body: bytes) -> Any:
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value.get("id") if isinstance(value, dict) else None


def create_app(hub: Hub | None = None, *, deadline_ms: int = DEFAULT_DEADLINE_MS) -> FastAPI:
    hub = hub or Hub()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hub = hub

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/device")
    async def device_link(ws: WebSocket) -> None:
        # Refuse before accepting: too many sockets from one address, or too many devices at all.
        ip = client_ip(ws.headers, ws.client.host if ws.client else None)
        if not hub.connect_limit.allow(ip) or len(hub.devices) >= MAX_DEVICES:
            await ws.close(code=1013)
            return
        await ws.accept()
        device = await _handshake(ws, hub)
        if device is None:
            return
        last_seen = asyncio.get_running_loop().time()

        async def keepalive() -> None:
            while True:
                await asyncio.sleep(PING_SECONDS)
                if asyncio.get_running_loop().time() - last_seen > IDLE_SECONDS:
                    await ws.close(code=4408)
                    return
                await device.send({"type": "ping"})

        pinger = asyncio.create_task(keepalive())
        try:
            while True:
                text = await ws.receive_text()
                last_seen = asyncio.get_running_loop().time()
                if len(text) > FRAME_LIMIT:
                    await ws.close(code=1009)
                    return
                try:
                    frame = RelayFrame.model_validate_json(text).root
                except ValidationError:
                    await ws.close(code=4400)
                    return
                kind = frame.type
                if kind == "ping":
                    await device.send({"type": "pong"})
                elif kind == "grant_add":
                    if len(device.grants) >= 50:
                        await ws.close(code=4429)
                        return
                    device.grants[str(frame.id)] = frame.hash  # type: ignore[union-attr]
                elif kind == "grant_remove":
                    device.grants.pop(str(frame.id), None)  # type: ignore[union-attr]
                elif kind in ("response", "chunk", "error", "cancel"):
                    rid = getattr(frame, "id", None)
                    pending = device.pending.get(rid) if rid else None
                    if pending is not None:
                        pending.frames.put_nowait(json.loads(text))
        except WebSocketDisconnect:
            pass
        finally:
            pinger.cancel()
            hub.unregister(device)

    @app.api_route("/m/{token}", methods=["GET", "DELETE"])
    async def not_allowed(token: str) -> Response:
        return Response(status_code=405, headers={"allow": "POST"})

    @app.post("/m/{token}")
    async def forward(token: str, request: Request) -> Response:
        match = TOKEN.match(token)
        if match is None:
            return Response(status_code=404)
        device_id, secret = match.groups()
        ip = client_ip(request.headers, request.client.host if request.client else None)
        if not hub.ip_limit.allow(ip) or not hub.device_limit.allow(device_id):
            return Response(status_code=429, headers={"retry-after": "30"})
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY):
            return Response(status_code=413)
        received = bytearray()
        async for chunk in request.stream():  # chunked uploads carry no length to check first
            received += chunk
            if len(received) > MAX_BODY:
                return Response(status_code=413)
        body = bytes(received)
        rpc_id = _request_id(body)
        device = hub.online(device_id)
        if device is None:
            return JSONResponse(_jsonrpc_error(rpc_id, -32001, OFFLINE))
        if not device.has_secret(secret):
            return Response(status_code=404)
        if len(device.pending) >= MAX_IN_FLIGHT:
            return JSONResponse(_jsonrpc_error(rpc_id, -32002, "Firra is busy"), status_code=429)
        return await _round_trip(device, request, secret, body, rpc_id, deadline_ms)

    return app


async def _handshake(ws: WebSocket, hub: Hub) -> Device | None:
    nonce = base64.b64encode(secrets.token_bytes(32)).decode()
    await ws.send_json({"type": "challenge", "nonce": nonce})
    try:
        text = await asyncio.wait_for(ws.receive_text(), HELLO_SECONDS)
        hello = Hello.model_validate_json(text)
        key = base64.b64decode(hello.public_key, validate=True)
        if len(key) != 32 or hashlib.sha256(key).hexdigest()[:16] != hello.device_id:
            raise ValueError("device id does not match the key")
        Ed25519PublicKey.from_public_bytes(key).verify(
            base64.b64decode(hello.signature, validate=True), base64.b64decode(nonce)
        )
    except (
        TimeoutError,
        WebSocketDisconnect,
        ValidationError,
        ValueError,
        binascii.Error,
        InvalidSignature,
    ):
        await ws.close(code=4401)
        return None
    lock = asyncio.Lock()

    async def send(frame: dict[str, Any]) -> None:
        async with lock:
            await ws.send_text(json.dumps(frame, separators=(",", ":")))

    device = Device(device_id=hello.device_id, send=send)
    previous = hub.register(device)
    if previous is not None:
        await previous.send({"type": "error", "code": "replaced"})
    await send({"type": "hello_ok"})
    log.info("device.connected")
    return device


async def _round_trip(
    device: Device, request: Request, secret: str, body: bytes, rpc_id: Any, deadline_ms: int
) -> Response:
    rid = secrets.token_urlsafe(12)
    pending = Pending()
    device.pending[rid] = pending
    headers = {k: request.headers[k] for k in ALLOWED_HEADERS if k in request.headers}
    headers["authorization"] = f"Bearer {secret}"
    try:
        await device.send(
            {
                "type": "request",
                "id": rid,
                "method": "POST",
                "path": "/mcp",
                "headers": headers,
                "body_b64": base64.b64encode(body).decode(),
                "deadline_ms": max(1000, deadline_ms),
            }
        )
        status, out_headers, data = await _collect(pending, deadline_ms / 1000)
    except (TimeoutError, asyncio.CancelledError):
        await _cancel(device, rid)
        return JSONResponse(
            _jsonrpc_error(rpc_id, -32003, "Firra is not answering"), status_code=504
        )
    except RuntimeError:
        return JSONResponse(_jsonrpc_error(rpc_id, -32001, OFFLINE))
    finally:
        device.pending.pop(rid, None)
    keep = {k: v for k, v in out_headers.items() if k.lower() in ("content-type", "mcp-session-id")}
    return Response(content=data, status_code=status, headers=keep)


async def _cancel(device: Device, rid: str) -> None:
    try:
        await device.send({"type": "cancel", "id": rid})
    except Exception:
        log.debug("cancel.failed")


async def _collect(pending: Pending, deadline: float) -> tuple[int, dict[str, str], bytes]:
    status = 200
    headers: dict[str, str] = {}
    data = bytearray()
    async with asyncio.timeout(deadline):
        while True:
            frame = await pending.frames.get()
            if frame["type"] == "error":
                raise RuntimeError(frame.get("code", "error"))
            if frame["type"] == "cancel":
                raise RuntimeError("cancelled")
            if frame["type"] == "response":
                status, headers = frame["status"], frame["headers"]
            data += base64.b64decode(frame["body_b64"])
            if len(data) > MAX_RESPONSE_BYTES:
                raise RuntimeError("too_large")
            if frame["final"]:
                return status, headers, bytes(data)


def main() -> None:  # pragma: no cover - process entry
    uvicorn.Server(
        server_config(
            create_app(),
            host="0.0.0.0",  # noqa: S104 - Cloud Run sets the interface
            port=int(os.environ.get("PORT", "8080")),
            log_level="warning",
        )
    ).run()


if __name__ == "__main__":  # pragma: no cover
    main()
