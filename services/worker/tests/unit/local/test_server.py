"""The local API frame refuses what the contract says it refuses (contracts.md section 3)."""

import pytest
from fastapi.testclient import TestClient

from firra.local.server import ApiError, create_local_app
from firra_contracts import EngineStatus

TOKEN = "t" * 40
PORT = 49152
BASE = f"http://127.0.0.1:{PORT}"


def _status() -> EngineStatus:
    return EngineStatus.model_validate(
        {
            "engine_version": "1.0.0",
            "database": "ready",
            "connections": [],
            "ai": {
                "executor": "conversation_only",
                "tools": [],
                "queue": {"pending": 0, "done": 0, "rejected_items": 0},
                "state": "conversation_only",
            },
            "relay": {"state": "offline"},
            "disk": {"data_bytes": 0},
        }
    )


@pytest.fixture
def client() -> TestClient:
    app = create_local_app(api_token=TOKEN, port=PORT, status=_status)

    @app.get("/boom")
    def boom() -> None:
        raise ApiError(404, "not_found")

    @app.get("/crash")
    def crash() -> None:
        raise RuntimeError("secret mail text jane@example.com")

    return TestClient(app, base_url=BASE, raise_server_exceptions=False)


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_status_with_the_token(client: TestClient) -> None:
    res = client.get("/local/status", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["database"] == "ready"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": TOKEN},
        {"Authorization": "Basic abc"},
    ],
)
def test_missing_or_wrong_token_is_401(client: TestClient, headers: dict[str, str]) -> None:
    res = client.get("/local/status", headers=headers)
    assert res.status_code == 401
    assert res.json()["error"] == "unauthorized"
    assert res.json()["request_id"]


def test_browser_requests_are_refused_even_with_the_token(client: TestClient) -> None:
    res = client.get("/local/status", headers={**AUTH, "Origin": "https://evil.example"})
    assert res.status_code == 403
    assert res.json()["error"] == "forbidden_origin"


def test_dns_rebinding_host_is_refused() -> None:
    app = create_local_app(api_token=TOKEN, port=PORT, status=_status)
    rebound = TestClient(app, base_url="http://attacker.example:49152")
    res = rebound.get("/local/status", headers=AUTH)
    assert res.status_code == 403
    assert res.json()["error"] == "bad_host"
    other_port = TestClient(app, base_url="http://127.0.0.1:1")
    assert other_port.get("/local/status", headers=AUTH).status_code == 403


def test_localhost_name_is_allowed(client: TestClient) -> None:
    local = TestClient(client.app, base_url=f"http://localhost:{PORT}")
    assert local.get("/local/status", headers=AUTH).status_code == 200


def test_oversize_and_bogus_length_are_refused(client: TestClient) -> None:
    big = client.post("/local/status", headers={**AUTH, "Content-Length": str(3 * 1024 * 1024)})
    assert big.status_code == 413
    bogus = client.post("/local/status", headers={**AUTH, "Content-Length": "abc"})
    assert bogus.status_code == 413


def test_api_error_uses_the_v1_model(client: TestClient) -> None:
    res = client.get("/boom", headers=AUTH)
    assert res.status_code == 404
    assert set(res.json()) == {"error", "message", "request_id"}
    assert res.json()["error"] == "not_found"


def test_unhandled_errors_never_echo_content(client: TestClient) -> None:
    res = client.get("/crash", headers=AUTH)
    assert res.status_code == 500
    assert "jane@example.com" not in res.text
    assert res.json()["error"] == "internal_error"


def test_no_docs_or_openapi_are_served(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, headers=AUTH).status_code == 404
