"""``/local/relay``: validation, one-time secret, hash-only storage, revocation."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from firra.local.readctx import ReadContext
from firra.local.routes.relay_links import build_router
from firra.local.server import ApiError
from firra.local.settings import load


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    ctx = ReadContext(pool=None, identity=None, home=tmp_path, engine_version="t")  # type: ignore[arg-type]
    app = FastAPI()
    app.include_router(
        build_router(
            ctx, device_id=lambda: "abcdef0123456789", state=lambda: "offline", changed=lambda: None
        )
    )

    @app.exception_handler(ApiError)
    def api_error(_: object, exc: ApiError) -> object:
        from fastapi.responses import JSONResponse

        return JSONResponse({"error": exc.code}, status_code=exc.status)

    return TestClient(app)


def test_links_need_a_relay_address_and_keep_only_the_hash(
    client: TestClient, tmp_path: Path
) -> None:
    assert client.post("/local/relay/links", json={"name": "claude.ai"}).status_code == 409
    for bad in ("http://relay.example", "https://a.b/path", 5):
        assert client.put("/local/relay", json={"relay_url": bad}).status_code == 422
    assert (
        client.put("/local/relay", json={"relay_url": "https://relay.example"}).status_code == 200
    )

    created = client.post(
        "/local/relay/links", json={"name": "claude.ai", "scopes": ["intelligence"]}
    )
    assert created.status_code == 200
    body = created.json()
    secret = body["url"].rsplit(".", 1)[1]
    assert body["url"].startswith("https://relay.example/m/abcdef0123456789.")
    listed = client.get("/local/relay")
    assert secret not in listed.text and "hash" not in listed.text
    assert secret not in (tmp_path / "settings.json").read_text()
    assert (tmp_path / "settings.json").stat().st_mode & 0o077 == 0
    assert listed.json()["links"][0]["scopes"] == ["intelligence"]

    for bad_body in ({"name": ""}, {"name": "x", "scopes": ["admin"]}, {"name": "x", "scopes": []}):
        assert client.post("/local/relay/links", json=bad_body).status_code == 422

    assert client.delete(f"/local/relay/links/{body['id']}").status_code == 204
    assert client.delete(f"/local/relay/links/{body['id']}").status_code == 404
    assert load(tmp_path).relay_links == []
