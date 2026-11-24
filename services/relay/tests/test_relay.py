"""Relay behaviour on a real socket: handshake, routing, offline, limits, hygiene."""

import asyncio
import base64
import functools
import json
import logging
from collections.abc import MutableMapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest
import websockets
from firra_relay.testing import Identity, Relay, connect, run

RPC = {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}


async def answer_once(
    ws: Any, *, status: int = 200, body: bytes = b'{"ok":true}'
) -> dict[str, Any]:
    frame: dict[str, Any] = json.loads(await ws.recv())
    while frame["type"] != "request":
        frame = json.loads(await ws.recv())
    await ws.send(
        json.dumps(
            {
                "type": "response",
                "id": frame["id"],
                "status": status,
                "headers": {"content-type": "application/json", "x-secret": "dropped"},
                "body_b64": base64.b64encode(body).decode(),
                "final": True,
            }
        )
    )
    return frame


def post(relay: Relay, token: str, **kwargs: Any) -> httpx.Response:
    return httpx.post(f"{relay.http}/m/{token}", timeout=10, **kwargs)


def test_forwards_a_call_and_returns_the_devices_answer(relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        ws = await connect(relay, me)
        task = asyncio.create_task(answer_once(ws, body=b'{"result":1}'))
        response = await asyncio.to_thread(
            post, relay, me.token, json=RPC, headers={"x-evil": "1", "accept": "application/json"}
        )
        frame = await task
        assert response.status_code == 200 and response.json() == {"result": 1}
        assert response.headers["content-type"] == "application/json"
        assert "x-secret" not in response.headers
        assert frame["method"] == "POST" and frame["path"] == "/mcp"
        assert frame["headers"]["authorization"] == f"Bearer {me.secret}"
        assert frame["headers"]["accept"] == "application/json" and "x-evil" not in frame["headers"]
        assert json.loads(base64.b64decode(frame["body_b64"])) == RPC
        await ws.close()

    run(scenario())


def test_chunked_answer_is_joined(relay: Relay) -> None:
    me = Identity()

    async def device(ws: Any) -> None:
        frame = json.loads(await ws.recv())
        rid = frame["id"]
        parts = [b"data: one\n\n", b"data: two\n\n", b"data: three\n\n"]
        await ws.send(
            json.dumps(
                {
                    "type": "response",
                    "id": rid,
                    "status": 200,
                    "headers": {"content-type": "text/event-stream"},
                    "body_b64": base64.b64encode(parts[0]).decode(),
                    "final": False,
                }
            )
        )
        for i, part in enumerate(parts[1:], start=1):
            await ws.send(
                json.dumps(
                    {
                        "type": "chunk",
                        "id": rid,
                        "seq": i,
                        "body_b64": base64.b64encode(part).decode(),
                        "final": i == 2,
                    }
                )
            )

    async def scenario() -> None:
        ws = await connect(relay, me)
        task = asyncio.create_task(device(ws))
        response = await asyncio.to_thread(post, relay, me.token, json=RPC)
        await task
        assert response.text == "data: one\n\ndata: two\n\ndata: three\n\n"
        assert response.headers["content-type"].startswith("text/event-stream")
        await ws.close()

    run(scenario())


def test_offline_device_answers_with_a_jsonrpc_error_keeping_the_id(relay: Relay) -> None:
    me = Identity()
    response = post(relay, me.token, json=RPC)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 7 and body["error"]["code"] == -32001
    assert "offline" in body["error"]["message"]


def test_bad_tokens_methods_and_unknown_secret(relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        ws = await connect(relay, me)
        for bad in (
            "nope",
            "zzzz." + "a" * 43,
            me.device_id + "." + "b" * 43,
            me.device_id + ".short",
        ):
            assert (await asyncio.to_thread(post, relay, bad, json=RPC)).status_code == 404
        got = await asyncio.to_thread(httpx.get, f"{relay.http}/m/{me.token}")
        deleted = await asyncio.to_thread(httpx.delete, f"{relay.http}/m/{me.token}")
        assert got.status_code == 405 and deleted.status_code == 405
        await ws.close()

    run(scenario())


def test_revoked_grant_stops_working_at_once(relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        ws = await connect(relay, me)
        task = asyncio.create_task(answer_once(ws))
        assert (await asyncio.to_thread(post, relay, me.token, json=RPC)).status_code == 200
        await task
        await ws.send(json.dumps({"type": "grant_remove", "id": me.grant}))
        await asyncio.sleep(0.1)
        assert (await asyncio.to_thread(post, relay, me.token, json=RPC)).status_code == 404
        await ws.close()

    run(scenario())


def test_wrong_signature_or_mismatched_device_id_is_refused(relay: Relay) -> None:
    me, other = Identity(), Identity()

    async def attempt(frame_builder: Any) -> int:
        ws = await websockets.connect(relay.ws)
        challenge = json.loads(await ws.recv())
        await ws.send(json.dumps(frame_builder(challenge["nonce"])))
        with pytest.raises(websockets.ConnectionClosed):
            await ws.recv()
        assert ws.close_code is not None
        return int(ws.close_code)

    async def scenario() -> None:
        forged = lambda nonce: {**me.hello(nonce), "signature": other.hello(nonce)["signature"]}  # noqa: E731
        wrong_id = lambda nonce: {**me.hello(nonce), "device_id": other.device_id}  # noqa: E731
        assert await attempt(forged) == 4401
        assert await attempt(wrong_id) == 4401
        assert await attempt(lambda nonce: {"type": "ping"}) == 4401

    run(scenario())
    assert relay.hub.devices == {}


def test_second_connection_replaces_the_first(relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        first = await connect(relay, me)
        second = await connect(relay, me)
        frames = []
        for _ in range(3):
            try:
                frames.append(json.loads(await asyncio.wait_for(first.recv(), 1)))
            except (TimeoutError, websockets.ConnectionClosed):
                break
        assert {"type": "error", "code": "replaced"} in frames
        await second.close()
        await first.close()

    run(scenario())


def test_silent_device_gets_a_timeout_error(fast_relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        ws = await connect(fast_relay, me)
        response = await asyncio.to_thread(post, fast_relay, me.token, json=RPC)
        assert response.status_code == 504 and response.json()["id"] == 7
        kinds = []
        try:
            while True:
                kinds.append(json.loads(await asyncio.wait_for(ws.recv(), 1))["type"])
        except TimeoutError:
            pass
        assert "request" in kinds and "cancel" in kinds
        await ws.close()

    run(scenario())


def test_in_flight_limit_and_oversize_body(relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        ws = await connect(relay, me)
        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor(max_workers=20)
        slow = [
            loop.run_in_executor(
                pool, functools.partial(post, relay, me.token, json={**RPC, "id": i})
            )
            for i in range(8)
        ]
        await asyncio.sleep(0.5)
        busy = await loop.run_in_executor(pool, lambda: post(relay, me.token, json=RPC))
        assert busy.status_code == 429 and busy.json()["error"]["code"] == -32002
        big = await asyncio.to_thread(post, relay, me.token, content=b"x" * (600 * 1024))
        assert big.status_code == 413
        for _ in range(8):
            await answer_once(ws)
        assert [r.status_code for r in await asyncio.gather(*slow)] == [200] * 8
        pool.shutdown()
        await ws.close()

    run(scenario())


def test_device_death_during_a_call_answers_offline(relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        ws = await connect(relay, me)
        call = asyncio.create_task(asyncio.to_thread(post, relay, me.token, json=RPC))
        await asyncio.sleep(0.3)
        await ws.close()
        response = await call
        assert response.status_code == 200 and response.json()["error"]["code"] == -32001

    run(scenario())


def test_no_secret_in_logs(relay: Relay, caplog: pytest.LogCaptureFixture) -> None:
    me = Identity()
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        ws = await connect(relay, me)
        task = asyncio.create_task(answer_once(ws))
        await asyncio.to_thread(post, relay, me.token, json=RPC)
        await task
        await ws.close()

    run(scenario())
    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("firra"))
    assert me.secret not in ours and me.hash not in ours


def test_connection_attempts_per_address_are_limited(relay: Relay) -> None:
    async def attempt() -> int | None:
        try:
            ws = await websockets.connect(relay.ws)
        except websockets.InvalidStatus as exc:
            return int(exc.response.status_code)
        await ws.close()
        return None

    async def scenario() -> None:
        results = [await attempt() for _ in range(35)]
        assert results[:30] == [None] * 30
        assert all(code == 403 for code in results[30:]), results[30:]

    run(scenario())


def test_forwarded_for_entries_before_the_last_cannot_dodge_the_address_limit(
    relay: Relay,
) -> None:
    """Cloud Run appends the real address last; whatever the caller put first is not trusted."""

    def call(i: int, real: str) -> int:
        token = f"{i:016x}.{'A' * 43}"
        return post(
            relay, token, json=RPC, headers={"x-forwarded-for": f"10.1.{i}.1, {real}"}
        ).status_code

    statuses = [call(i, "203.0.113.9") for i in range(250)]
    assert 429 in statuses and statuses.index(429) <= 240
    # Another real client has its own budget, so one noisy address cannot lock out the rest.
    assert call(999, "203.0.113.10") == 200


def test_chunked_upload_is_cut_off_at_the_body_limit() -> None:
    """No length is declared, so the relay must stop reading at the limit, not after the upload."""
    from firra_relay.app import MAX_BODY, create_app

    reads = 0

    async def receive() -> MutableMapping[str, Any]:
        nonlocal reads
        reads += 1
        assert reads < 500, "the relay kept reading an oversize upload"
        return {"type": "http.request", "body": b"x" * 65536, "more_body": True}

    sent: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": f"/m/{'a' * 16}.{'B' * 43}",
        "raw_path": b"",
        "query_string": b"",
        "headers": [],
        "client": ("203.0.113.1", 1234),
        "server": ("relay.test", 443),
        "scheme": "https",
    }
    asyncio.run(create_app()(scope, receive, send))
    assert sent[0]["status"] == 413
    assert reads <= MAX_BODY // 65536 + 2


def test_a_full_queue_still_hears_that_the_device_is_gone() -> None:
    from firra_relay.hub import Device, Hub, Pending

    async def scenario() -> None:
        async def send(frame: dict[str, Any]) -> None:
            return None

        hub = Hub()
        device = Device(device_id="d" * 16, send=send)
        hub.register(device)
        stuck, other = Pending(), Pending()
        for _ in range(64):
            stuck.frames.put_nowait({"type": "chunk"})
        device.pending.update({"a": stuck, "b": other})
        hub.unregister(device)
        assert [stuck.frames.get_nowait() for _ in range(stuck.frames.qsize())][-1] == {
            "type": "error",
            "code": "device_gone",
        }
        assert other.frames.get_nowait() == {"type": "error", "code": "device_gone"}

    asyncio.run(scenario())
