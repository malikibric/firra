"""MCP SDK 2.x mounting and owner-setting opt-in, without any provider calls."""

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.mcpserver import MCPServer

from firra.local.mcp.transport import http_router
from firra.local.settings import OwnerSettings, load, save


def test_sdk_stateless_router_handles_requests_without_parent_lifespan() -> None:
    server: MCPServer[Any] = MCPServer("firra-test")

    @server.tool()
    def synthetic() -> str:
        return "fixture"

    app = FastAPI()
    app.include_router(http_router(server))
    http = TestClient(app, base_url="http://127.0.0.1:49152")
    for _ in range(2):
        response = http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"accept": "application/json, text/event-stream"},
        )
        assert response.status_code == 200
        assert [t["name"] for t in response.json()["result"]["tools"]] == ["synthetic"]
    assert http.get("/mcp").status_code == 405


def test_owner_settings_default_off_atomic_mode_and_reloaded(tmp_path: Path) -> None:
    assert load(tmp_path).mcp_source_access is False
    save(OwnerSettings(mcp_source_access=True), tmp_path)
    assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600
    assert load(tmp_path).mcp_source_access is True
    save(OwnerSettings(), tmp_path)
    assert load(tmp_path).mcp_source_access is False
    assert list(tmp_path.glob(".settings-*")) == []
    (tmp_path / "settings.json").write_text('{"mcp_evidence_context": true}')
    assert load(tmp_path).mcp_source_access is True  # files from before the source layer
    (tmp_path / "settings.json").write_text('{"mcp_evidence_context":"true"}')
    with pytest.raises(ValueError, match="invalid owner settings"):
        load(tmp_path)
