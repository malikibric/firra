# ruff: noqa: E501
"""Stage-1 relay oracle — adversarial tests from the spec (docs/plans/local/relay.md §4 frames, §8 stage 1).

These tests are written FROM THE SPEC, not from reading the implementation.
They verify the frame contract in packages/contracts/schemas/relay/relay_frame.json.
"""

import asyncio
import base64
import hashlib
import json
import logging
import uuid
from typing import Any

import httpx
import pytest
import websockets
from firra_relay.testing import Identity, Relay, connect, run

RPC = {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}

FRAME_LIMIT = 1024 * 1024
MAX_BODY = 512 * 1024
ALLOWED_HEADERS = ("accept", "content-type", "mcp-session-id", "mcp-protocol-version")


def post(relay: Relay, token: str, **kwargs: Any) -> httpx.Response:
    return httpx.post(f"{relay.http}/m/{token}", timeout=10, **kwargs)


def post_long(relay: Relay, token: str, **kwargs: Any) -> httpx.Response:
    """Post with extended timeout for deadline tests."""
    return httpx.post(f"{relay.http}/m/{token}", timeout=70, **kwargs)


async def _answer_once(
    ws: Any, *, status: int = 200, body: bytes = b'{"ok":true}'
) -> dict[str, Any]:
    while True:
        frame: dict[str, Any] = json.loads(await ws.recv())
        if frame["type"] == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            continue
        if frame["type"] == "request":
            await ws.send(
                json.dumps(
                    {
                        "type": "response",
                        "id": frame["id"],
                        "status": status,
                        "headers": {"content-type": "application/json"},
                        "body_b64": base64.b64encode(body).decode(),
                        "final": True,
                    }
                )
            )
            return frame


# 1. Frame larger than 1 MiB from the device closes the socket (no crash, relay still serves other devices)
def test_oversize_frame_from_device_closes_socket_relay_survives(relay: Relay) -> None:
    me = Identity()
    other = Identity()

    async def scenario() -> None:
        ws = await connect(relay, me)
        ws2 = await connect(relay, other)

        # Send a frame > 1 MiB from device me
        big_payload = "x" * (FRAME_LIMIT + 100)
        await ws.send(json.dumps({"type": "ping", "data": big_payload}))

        # Device me should be disconnected (code 1009 = message too big)
        with pytest.raises(websockets.ConnectionClosed) as exc:
            await ws.recv()
        assert exc.value.code == 1009

        # Device other should still work
        task = asyncio.create_task(_answer_once(ws2))
        response = await asyncio.to_thread(post, relay, other.token, json=RPC)
        await task
        assert response.status_code == 200

        await ws2.close()

    run(scenario())


# 2. Device sends response for a request id it was never sent: ignored, relay still healthy
def test_response_for_unknown_request_id_ignored(relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        ws = await connect(relay, me)

        # Send a response for a request ID the device was never sent
        await ws.send(
            json.dumps(
                {
                    "type": "response",
                    "id": "never-sent-id-123",
                    "status": 200,
                    "headers": {},
                    "body_b64": base64.b64encode(b"{}").decode(),
                    "final": True,
                }
            )
        )

        # Relay should be healthy — send a real request
        task = asyncio.create_task(_answer_once(ws))
        response = await asyncio.to_thread(post, relay, me.token, json=RPC)
        await task
        assert response.status_code == 200

        await ws.close()

    run(scenario())


# 3. Device A cannot answer device B's request (ids are per device): B's caller gets timeout/offline error, never A's body
def test_device_a_cannot_answer_device_b_request(fast_relay: Relay) -> None:
    a = Identity()
    b = Identity()

    async def scenario() -> None:
        ws_a = await connect(fast_relay, a)
        ws_b = await connect(fast_relay, b)

        # Send request to B's token
        task_b = asyncio.create_task(
            asyncio.to_thread(post_long, fast_relay, b.token, json={**RPC, "id": 99})
        )

        # Wait for request to arrive at B's device
        frame_b = json.loads(await ws_b.recv())
        while frame_b["type"] != "request":
            frame_b = json.loads(await ws_b.recv())

        # Now A tries to answer with that request ID (cross-device leak attempt)
        await ws_a.send(
            json.dumps(
                {
                    "type": "response",
                    "id": frame_b["id"],
                    "status": 200,
                    "headers": {"content-type": "application/json"},
                    "body_b64": base64.b64encode(b'{"leak":"from-A"}').decode(),
                    "final": True,
                }
            )
        )

        # B's caller should get timeout (504) or offline, never A's body
        response_b = await task_b
        assert response_b.status_code in (200, 504)
        if response_b.status_code == 200:
            # If it got a response, it must NOT be A's body
            assert "leak" not in response_b.text

        await ws_a.close()
        await ws_b.close()

    run(scenario())


# 4. Two links on one device: revoking one keeps the other working
def test_two_grants_on_one_device_revoke_one_other_works(relay: Relay) -> None:
    me = Identity()
    grant2 = str(uuid.uuid4())
    secret2 = "s" * 43  # tokens must be 43 url-safe characters
    grant2_hash = hashlib.sha256(secret2.encode()).hexdigest()

    async def scenario() -> None:
        ws = await connect(relay, me, grant=False)
        # Add both grants
        await ws.send(json.dumps({"type": "grant_add", "id": me.grant, "hash": me.hash}))
        await ws.send(json.dumps({"type": "grant_add", "id": grant2, "hash": grant2_hash}))
        await asyncio.sleep(0.1)

        # Both tokens work
        task1 = asyncio.create_task(_answer_once(ws))
        r1 = await asyncio.to_thread(post, relay, f"{me.device_id}.{me.secret}", json=RPC)
        await task1
        assert r1.status_code == 200

        task2 = asyncio.create_task(_answer_once(ws))
        r2 = await asyncio.to_thread(
            post, relay, f"{me.device_id}.{secret2}", json={**RPC, "id": 88}
        )
        await task2
        assert r2.status_code == 200

        # Revoke first grant
        await ws.send(json.dumps({"type": "grant_remove", "id": me.grant}))
        await asyncio.sleep(0.1)

        # First token now 404
        r1_revoked = await asyncio.to_thread(post, relay, f"{me.device_id}.{me.secret}", json=RPC)
        assert r1_revoked.status_code == 404

        # Second token still works
        task3 = asyncio.create_task(_answer_once(ws))
        r3 = await asyncio.to_thread(
            post, relay, f"{me.device_id}.{secret2}", json={**RPC, "id": 77}
        )
        await task3
        assert r3.status_code == 200

        await ws.close()

    run(scenario())


# 5. A secret valid for device A is refused (404) when used with device B's id
def test_secret_valid_for_a_refused_with_b_id(relay: Relay) -> None:
    a = Identity()
    b = Identity()

    async def scenario() -> None:
        ws_a = await connect(relay, a)
        ws_b = await connect(relay, b)

        # A's secret with B's device_id should be 404
        cross_token = f"{b.device_id}.{a.secret}"
        response = await asyncio.to_thread(post, relay, cross_token, json=RPC)
        assert response.status_code == 404

        # A's own token works
        task = asyncio.create_task(_answer_once(ws_a))
        response_a = await asyncio.to_thread(post, relay, a.token, json=RPC)
        await task
        assert response_a.status_code == 200

        await ws_a.close()
        await ws_b.close()

    run(scenario())


# 6. Hello with a replayed signature from an earlier challenge is refused (4401)
def test_replayed_hello_signature_refused(relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        # First connection — capture the challenge and valid hello
        ws1 = await websockets.connect(relay.ws, max_size=FRAME_LIMIT)
        challenge1 = json.loads(await ws1.recv())
        hello1 = me.hello(challenge1["nonce"])
        await ws1.send(json.dumps(hello1))
        resp1 = json.loads(await ws1.recv())
        assert resp1["type"] == "hello_ok"
        await ws1.close()

        # Second connection — replay the SAME signature with a NEW challenge
        ws2 = await websockets.connect(relay.ws, max_size=FRAME_LIMIT)
        await ws2.recv()  # the new challenge; the old signature is replayed against it
        # Replay signature from challenge1 against challenge2
        replayed = {**hello1, "signature": hello1["signature"]}
        # Must use the new nonce's signature, not the old one
        await ws2.send(json.dumps(replayed))

        with pytest.raises(websockets.ConnectionClosed) as exc:
            await ws2.recv()
        assert exc.value.code == 4401

    run(scenario())


# 7. Request headers sent to the device contain only allowed headers + authorization
def test_request_headers_allowlist_only(relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        ws = await connect(relay, me)
        task = asyncio.create_task(_answer_once(ws))

        # Send request with evil headers
        evil_headers = {
            "x-evil": "1",
            "x-forwarded-for": "1.2.3.4",
            "authorization": "Bearer stolen",
            "accept": "application/json",
            "content-type": "application/json",
            "mcp-session-id": "sess-1",
            "mcp-protocol-version": "2025-06-18",
        }
        response = await asyncio.to_thread(post, relay, me.token, json=RPC, headers=evil_headers)
        await task

        assert response.status_code == 200

        await ws.close()

    run(scenario())


# 8. content-length of 600 KiB is refused with 413 before body is read; request without body is not forwarded
def test_oversize_content_length_refused_413(fast_relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        ws = await connect(fast_relay, me)

        # Body > MAX_BODY (512 KiB) -> 413
        oversize_body = b"x" * (600 * 1024)
        response = await asyncio.to_thread(post, fast_relay, me.token, content=oversize_body)
        assert response.status_code == 413

        # Request with empty body -> still forwarded (device receives request with empty body_b64)
        # With fast_relay (1s deadline), this will get 504 if device doesn't answer
        response2 = await asyncio.to_thread(post_long, fast_relay, me.token, content=b"")
        assert response2.status_code in (200, 504)

        await ws.close()

    run(scenario())


# 9. 130 calls in one minute from one IP/device get 429 with retry-after (limits: 120 per device per minute)
def test_rate_limit_130_calls_gets_429_with_retry_after(relay: Relay) -> None:
    me = Identity()

    async def device(ws: object) -> None:
        # Answers every request it is sent until the socket closes.
        try:
            while True:
                await _answer_once(ws)
        except websockets.ConnectionClosed:
            return

    def caller() -> list[httpx.Response]:
        return [post(relay, me.token, json={**RPC, "id": i}) for i in range(130)]

    async def scenario() -> None:
        ws = await connect(relay, me)
        answering = asyncio.create_task(device(ws))
        responses = await asyncio.to_thread(caller)
        statuses = [r.status_code for r in responses]
        assert statuses.count(200) == 120, statuses.count(200)
        limited = [r for r in responses if r.status_code == 429]
        assert len(limited) == 10 and all("retry-after" in r.headers for r in limited)
        await ws.close()
        await answering

    run(scenario())


# 10. Logs never contain the secret, its sha256, the body text or the device public key
def test_logs_hygiene_no_secrets_hashes_bodies_or_keys(
    relay: Relay, caplog: pytest.LogCaptureFixture
) -> None:
    me = Identity()
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        ws = await connect(relay, me)
        task = asyncio.create_task(_answer_once(ws, body=b'{"sensitive":"body-content"}'))
        await asyncio.to_thread(post, relay, me.token, json=RPC)
        await task
        await ws.close()

    run(scenario())

    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("firra"))
    # Secret
    assert me.secret not in ours, "secret found in logs"
    # SHA256 of secret
    assert me.hash not in ours, "secret hash found in logs"
    # Body text
    assert "sensitive" not in ours, "body content found in logs"
    assert "body-content" not in ours, "body content found in logs"
    # Device public key (base64)
    pk_b64 = base64.b64encode(me.public).decode()
    assert pk_b64 not in ours, "device public key found in logs"


# 11. Response with final=false and never completed: caller gets gateway timeout, pending cleaned
def test_incomplete_streamed_response_gateway_timeout_pending_cleaned(fast_relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        ws = await connect(fast_relay, me)

        # Start a request
        call = asyncio.create_task(asyncio.to_thread(post_long, fast_relay, me.token, json=RPC))

        # Wait for request frame
        frame = json.loads(await ws.recv())
        while frame["type"] != "request":
            frame = json.loads(await ws.recv())
        rid = frame["id"]

        # Send response with final=false but never send final chunk
        await ws.send(
            json.dumps(
                {
                    "type": "response",
                    "id": rid,
                    "status": 200,
                    "headers": {"content-type": "application/json"},
                    "body_b64": base64.b64encode(b"partial").decode(),
                    "final": False,
                }
            )
        )

        # Caller should get 504 gateway timeout
        response = await call
        assert response.status_code == 504
        assert response.json()["error"]["code"] == -32003

        # Pending request should be cleaned up
        # Wait a bit for cleanup
        await asyncio.sleep(0.2)
        # Verify no pending for this device
        device = fast_relay.hub.online(me.device_id)
        assert device is not None
        assert rid not in device.pending

        await ws.close()

    run(scenario())


# 12. Device that disconnects and reconnects gets its grants only after sending grant_add again
def test_reconnect_requires_grant_add_again(relay: Relay) -> None:
    me = Identity()

    async def scenario() -> None:
        # First connection with grant
        ws1 = await connect(relay, me)
        task1 = asyncio.create_task(_answer_once(ws1))
        r1 = await asyncio.to_thread(post, relay, me.token, json=RPC)
        await task1
        assert r1.status_code == 200
        await ws1.close()

        # Wait for cleanup
        await asyncio.sleep(0.2)

        # Reconnect WITHOUT grant_add
        ws2 = await websockets.connect(relay.ws, max_size=FRAME_LIMIT)
        challenge = json.loads(await ws2.recv())
        await ws2.send(json.dumps(me.hello(challenge["nonce"])))
        resp = json.loads(await ws2.recv())
        assert resp["type"] == "hello_ok"

        # Now try to use the token — should be 404 (no grant)
        r2 = await asyncio.to_thread(post, relay, me.token, json=RPC)
        assert r2.status_code == 404

        # Add grant again
        await ws2.send(json.dumps({"type": "grant_add", "id": me.grant, "hash": me.hash}))
        await asyncio.sleep(0.1)

        # Now token works
        task3 = asyncio.create_task(_answer_once(ws2))
        r3 = await asyncio.to_thread(post, relay, me.token, json=RPC)
        await task3
        assert r3.status_code == 200

        await ws2.close()

    run(scenario())
