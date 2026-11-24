"""Engine device link against a real relay and a fake local MCP endpoint."""

import json
import threading
import time
from collections.abc import Iterator
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

    def do_POST(self) -> None:
        length = int(self.headers["content-length"])
        body = self.rfile.read(length)
        FakeMcp.seen.append(dict(self.headers.items()))
        out = json.dumps({"jsonrpc": "2.0", "id": json.loads(body)["id"], "result": {"ok": 1}})
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(out.encode())

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture()
def relay() -> Iterator[Relay]:
    yield from start(60_000)


@pytest.fixture()
def local_mcp() -> Iterator[int]:
    FakeMcp.seen = []
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


def test_link_roundtrip_scopes_revocation(tmp_path: Path, relay: Relay, local_mcp: int) -> None:
    home = tmp_path
    save(load(home).model_copy(update={"relay_url": relay.http}), home)
    client = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    client.start()
    try:
        wait_for(lambda: client.state == "online")
        link, token = links.create(home, "claude.ai", ["intelligence"], device=client.device_id)
        client.notify()
        wait_for(lambda: relay.hub.devices[client.device_id].grants)

        ok = call(relay, token)
        assert ok.json()["result"] == {"ok": 1}
        seen = FakeMcp.seen[-1]
        assert seen["authorization"] == f"Bearer {LOCAL_TOKEN}"
        assert seen["x-firra-scopes"] == "intelligence"
        assert token.split(".")[1] not in json.dumps(seen)

        # An unknown secret never reaches the engine.
        assert call(relay, f"{client.device_id}.{'a' * 43}").status_code == 404

        assert links.revoke(home, link.id)
        client.notify()
        wait_for(lambda: not relay.hub.devices[client.device_id].grants)
        assert call(relay, token).status_code == 404
        assert len(FakeMcp.seen) == 1
    finally:
        client.stop()


def test_engine_rechecks_the_secret_itself(tmp_path: Path, relay: Relay, local_mcp: int) -> None:
    """The relay has a grant the engine does not know: the engine answers 401 and no MCP call."""
    home = tmp_path
    save(load(home).model_copy(update={"relay_url": relay.http}), home)
    client = RelayClient(home, lambda: KEY, lambda: (local_mcp, LOCAL_TOKEN))
    client.start()
    try:
        wait_for(lambda: client.state == "online")
        _, token = links.create(home, "x", ["intelligence"], device=client.device_id)
        client.notify()
        wait_for(lambda: relay.hub.devices[client.device_id].grants)
        save(load(home).model_copy(update={"relay_links": []}), home)  # no notify: relay is stale
        assert call(relay, token).status_code == 401
        assert FakeMcp.seen == []
    finally:
        client.stop()
