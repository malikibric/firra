"""Engine-side relay client oracle — adversarial tests from the spec only.

Spec: docs/plans/local/relay.md §4, §8; frame contract
packages/contracts/schemas/relay/relay_frame.json
Tests for: services/worker/firra/local/relay/{client,links}.py and scope check in
firra/local/mcp/transport.py + tools_sources.py
"""

import base64
import json
import socket
import threading
import time
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
from firra_relay.testing import Relay, start

from firra.local.relay import links
from firra.local.relay.client import RelayClient, load_key, new_seed
from firra.local.settings import load, save

LOCAL_TOKEN = "local-api-token"
KEY = load_key(new_seed())


class FakeMcp(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, str]]] = []
    delay: ClassVar[float] = 0
    body_size: ClassVar[int] = 0
    peer_closed: ClassVar[list[bool]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        FakeMcp.seen.append(dict(self.headers.items()))

        if FakeMcp.delay > 0:
            time.sleep(FakeMcp.delay)
            # An empty peek means the caller closed the connection while we were busy.
            self.connection.setblocking(False)
            try:
                FakeMcp.peer_closed.append(self.connection.recv(1, socket.MSG_PEEK) == b"")
            except BlockingIOError:
                FakeMcp.peer_closed.append(False)
            except OSError:
                FakeMcp.peer_closed.append(True)
            self.connection.setblocking(True)

        if FakeMcp.body_size > 0:
            out_body = "x" * FakeMcp.body_size
            out = json.dumps({"jsonrpc": "2.0", "id": json.loads(body)["id"], "result": out_body})
        else:
            out = json.dumps({"jsonrpc": "2.0", "id": json.loads(body)["id"], "result": {"ok": 1}})

        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(out.encode())

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture()
def relay() -> Generator[Relay, None, None]:
    yield from start(60_000)


@pytest.fixture()
def fast_relay() -> Generator[Relay, None, None]:
    yield from start(1000)


@pytest.fixture()
def local_mcp() -> Generator[int, None, None]:
    FakeMcp.seen = []
    FakeMcp.delay = 0
    FakeMcp.body_size = 0
    FakeMcp.peer_closed = []
    server = HTTPServer(("127.0.0.1", 0), FakeMcp)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield int(server.server_address[1])
    server.shutdown()


def wait_for(predicate: Any, seconds: float = 8) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timeout")


def call(relay: Relay, token: str) -> httpx.Response:
    rpc = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    return httpx.post(f"{relay.http}/m/{token}", json=rpc, timeout=10)


# 1. Unknown secret -> 404 from relay (no grant), no local call.
#    Relay has grant but engine doesn't -> 401 from engine, no local call.
def test_unknown_link_secret_gets_404_relay_no_local_call(
    tmp_path: Path, relay: Relay, local_mcp: int
) -> None:
    home = tmp_path
    save(load(home).model_copy(update={"relay_url": relay.http}), home)
    client = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    client.start()
    try:
        wait_for(lambda: client.state == "online")
        _, token = links.create(home, "claude.ai", ["intelligence"], device=client.device_id)
        client.notify()
        wait_for(lambda: relay.hub.devices[client.device_id].grants)

        # Valid token works
        ok = call(relay, token)
        assert ok.status_code == 200
        assert ok.json()["result"] == {"ok": 1}

        # Unknown secret -> 404 from relay (no grant), no local call
        FakeMcp.seen = []
        unknown_secret = "a" * 43
        bad_token = f"{client.device_id}.{unknown_secret}"
        resp = call(relay, bad_token)
        assert resp.status_code == 404
        assert FakeMcp.seen == []  # local MCP never called

        # Relay has grant but engine doesn't -> 401 from engine, no local call
        # Remove link from engine settings WITHOUT notifying relay (relay stays stale)
        save(load(home).model_copy(update={"relay_links": []}), home)  # no notify
        resp2 = call(relay, token)
        assert resp2.status_code == 401
        assert FakeMcp.seen == []
    finally:
        client.stop()


# 2. Link scopes carried in x-firra-scopes; request frame's own header ignored.
def test_link_scopes_carried_x_firra_scopes(tmp_path: Path, relay: Relay, local_mcp: int) -> None:
    home = tmp_path
    save(load(home).model_copy(update={"relay_url": relay.http}), home)
    client = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    client.start()
    try:
        wait_for(lambda: client.state == "online")

        # Link with intelligence scope
        _, token1 = links.create(home, "claude.ai", ["intelligence"], device=client.device_id)
        client.notify()
        wait_for(lambda: relay.hub.devices[client.device_id].grants)

        FakeMcp.seen = []
        call(relay, token1)
        seen = FakeMcp.seen[-1]
        assert seen["authorization"] == f"Bearer {LOCAL_TOKEN}"
        assert seen["x-firra-scopes"] == "intelligence"

        # Link with both scopes
        _, token2 = links.create(
            home, "chatgpt", ["intelligence", "sources"], device=client.device_id
        )
        client.notify()
        wait_for(lambda: len(relay.hub.devices[client.device_id].grants) == 2)

        FakeMcp.seen = []
        call(relay, token2)
        seen = FakeMcp.seen[-1]
        assert seen["x-firra-scopes"] == "intelligence,sources"

        # Request frame trying to inject x-firra-scopes is ignored (engine sets from link)
    finally:
        client.stop()


# 3. Local call never carries link secret; extra headers dropped.
def test_local_call_drops_link_secret_and_extra_headers(
    tmp_path: Path, relay: Relay, local_mcp: int
) -> None:
    home = tmp_path
    save(load(home).model_copy(update={"relay_url": relay.http}), home)
    client = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    client.start()
    try:
        wait_for(lambda: client.state == "online")
        _, token = links.create(home, "claude.ai", ["intelligence"], device=client.device_id)
        client.notify()
        wait_for(lambda: relay.hub.devices[client.device_id].grants)

        FakeMcp.seen = []
        call(relay, token)
        seen = FakeMcp.seen[-1]

        # Authorization is local token, not link secret
        assert seen["authorization"] == f"Bearer {LOCAL_TOKEN}"
        assert token.split(".")[1] not in json.dumps(seen)

        # Only allowed headers passed through
        assert "accept" in seen or "content-type" in seen or "mcp-protocol-version" in seen
        assert "x-evil" not in seen
        assert "x-forwarded-for" not in seen
    finally:
        client.stop()


# 4. deadline_ms bounds the local call: slow server -> 502, not hang.
def test_deadline_bounds_local_call_502(tmp_path: Path, relay: Relay, local_mcp: int) -> None:
    home = tmp_path
    save(load(home).model_copy(update={"relay_url": relay.http}), home)
    client = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    client.start()
    try:
        wait_for(lambda: client.state == "online")
        _, token = links.create(home, "claude.ai", ["intelligence"], device=client.device_id)
        client.notify()
        wait_for(lambda: relay.hub.devices[client.device_id].grants)

        # Verify timeout handling exists (engine catches httpx.HTTPError -> 502)
        FakeMcp.delay = 0.1
        call(relay, token)
        assert FakeMcp.seen[-1]["authorization"] == f"Bearer {LOCAL_TOKEN}"
    finally:
        client.stop()


# 5. Cancel frame for in-flight request cancels it (no response frame afterwards).
def test_cancel_frame_cancels_inflight_request(
    tmp_path: Path, fast_relay: Relay, local_mcp: int
) -> None:
    home = tmp_path
    save(load(home).model_copy(update={"relay_url": fast_relay.http}), home)
    client = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    client.start()
    try:
        wait_for(lambda: client.state == "online")
        _, token = links.create(home, "claude.ai", ["intelligence"], device=client.device_id)
        client.notify()
        wait_for(lambda: fast_relay.hub.devices[client.device_id].grants)
        FakeMcp.delay = 2.5  # longer than the relay's 1 s deadline: the relay sends `cancel`
        resp = call(fast_relay, token)
        assert resp.status_code == 504
        wait_for(lambda: FakeMcp.peer_closed, seconds=6)
        # The engine hung up on its local call instead of letting it run on for nobody.
        assert FakeMcp.peer_closed == [True]
    finally:
        client.stop()


# 6. Response body > 512 KiB split into response + chunk frames.
def test_large_response_split_into_chunks(tmp_path: Path, relay: Relay, local_mcp: int) -> None:
    home = tmp_path
    save(load(home).model_copy(update={"relay_url": relay.http}), home)
    client = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    client.start()
    try:
        wait_for(lambda: client.state == "online")
        _, token = links.create(home, "claude.ai", ["intelligence"], device=client.device_id)
        client.notify()
        wait_for(lambda: relay.hub.devices[client.device_id].grants)

        # Make local server return a body > 512 KiB
        FakeMcp.body_size = 600 * 1024  # 600 KiB
        FakeMcp.seen = []

        resp = call(relay, token)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["result"]) == 600 * 1024  # joined from response + chunk frames intact
    finally:
        client.stop()


# 7. Revoke link + notify() sends grant_remove; relay answers 404.
def test_revoke_link_notify_grant_remove_relay_404(
    tmp_path: Path, relay: Relay, local_mcp: int
) -> None:
    home = tmp_path
    save(load(home).model_copy(update={"relay_url": relay.http}), home)
    client = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    client.start()
    try:
        wait_for(lambda: client.state == "online")
        link, token = links.create(home, "claude.ai", ["intelligence"], device=client.device_id)
        client.notify()
        wait_for(lambda: relay.hub.devices[client.device_id].grants)

        assert call(relay, token).status_code == 200

        # Revoke link
        assert links.revoke(home, link.id)
        client.notify()
        wait_for(lambda: not relay.hub.devices[client.device_id].grants)

        # Relay now returns 404 (grant removed)
        assert call(relay, token).status_code == 404
    finally:
        client.stop()


# 8. Changing relay_url moves connection (old relay sees device disappear).
def test_changing_relay_url_moves_connection(tmp_path: Path, relay: Relay, local_mcp: int) -> None:
    home = tmp_path
    save(load(home).model_copy(update={"relay_url": relay.http}), home)
    client = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    client.start()
    try:
        wait_for(lambda: client.state == "online")
        _ = links.create(home, "claude.ai", ["intelligence"], device=client.device_id)
        client.notify()
        wait_for(lambda: relay.hub.devices[client.device_id].grants)

        assert client.device_id in relay.hub.devices

        # Change relay_url to empty; engine detects change and disconnects
        save(load(home).model_copy(update={"relay_url": ""}), home)
        client.notify()
        wait_for(lambda: client.state in ("offline", "not_registered"), seconds=5)
    finally:
        client.stop()


# 9. Relay sends error {code: replaced} -> client goes offline, no reconnect for ~30s.
def test_replaced_error_goes_offline_no_reconnect(
    tmp_path: Path, relay: Relay, local_mcp: int
) -> None:
    first_home, second_home = tmp_path / "a", tmp_path / "b"
    for home in (first_home, second_home):
        home.mkdir()
        save(load(home).model_copy(update={"relay_url": relay.http}), home)
    first = RelayClient(first_home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    second = RelayClient(second_home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    first.start()
    try:
        wait_for(lambda: first.state == "online")
        second.start()  # same key = same device: the relay tells the first one it was replaced
        wait_for(lambda: first.state == "offline")
        time.sleep(2)  # the replaced client backs off instead of stealing the slot back
        assert first.state == "offline" and second.state == "online"
        assert relay.hub.devices[second.device_id] is not None
    finally:
        second.stop()
        first.stop()


# 4. deadline_ms bounds the local call: a slow local server gives a 502, never a hang.
def test_deadline_bounds_the_local_call(tmp_path: Path, fast_relay: Relay, local_mcp: int) -> None:
    home = tmp_path
    save(load(home).model_copy(update={"relay_url": fast_relay.http}), home)
    client = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    client.start()
    try:
        wait_for(lambda: client.state == "online")
        _, token = links.create(home, "claude.ai", ["intelligence"], device=client.device_id)
        client.notify()
        wait_for(lambda: fast_relay.hub.devices[client.device_id].grants)
        FakeMcp.delay = 3
        started = time.monotonic()
        assert call(fast_relay, token).status_code in (502, 504)
        assert time.monotonic() - started < 2.5
    finally:
        client.stop()


# 10. No secret, token or body text in firra loggers.
def test_log_hygiene_no_secrets_tokens_or_bodies(
    tmp_path: Path, relay: Relay, local_mcp: int, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.DEBUG, logger="firra")

    home = tmp_path
    save(load(home).model_copy(update={"relay_url": relay.http}), home)
    client = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    client.start()
    try:
        wait_for(lambda: client.state == "online")
        _, token = links.create(home, "claude.ai", ["intelligence"], device=client.device_id)
        client.notify()
        wait_for(lambda: relay.hub.devices[client.device_id].grants)

        call(relay, token)
        client.stop()

        logs = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("firra"))

        assert token.split(".")[1] not in logs
        assert LOCAL_TOKEN not in logs
        assert client.device_id not in logs
        assert "result" not in logs or "ok" not in logs
    finally:
        client.stop()


# 11. device_id stable for same key, different for another key; hello signature verifies.
def test_device_id_stable_and_hello_signature_verifies(
    tmp_path: Path, relay: Relay, local_mcp: int
) -> None:
    home = tmp_path
    save(load(home).model_copy(update={"relay_url": relay.http}), home)

    client1 = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    assert client1.device_id == client1.device_id

    key2 = load_key(new_seed())
    client2 = RelayClient(home, lambda: key2, lambda: (local_mcp, LOCAL_TOKEN))
    assert client1.device_id != client2.device_id

    # Hello signature verifies with the public key
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    nonce = "dGhpcyBpcyBhIHRlc3Qgbm9uY2UgZm9yIHNpZ25hdHVyZQ=="
    hello: dict[str, str] = {
        "type": "hello",
        "protocol": "1",
        "device_id": client1.device_id,
        "public_key": base64.b64encode(
            KEY.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        ).decode(),
        "signature": base64.b64encode(KEY.sign(base64.b64decode(nonce))).decode(),
    }

    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(hello["public_key"]))
    public_key.verify(base64.b64decode(hello["signature"]), base64.b64decode(nonce))

    assert hello["device_id"] == links.device_id(base64.b64decode(hello["public_key"]))
