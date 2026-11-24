"""Capture default and processing-enabled MCP settings from the real route with a scratch home.

Run from the repository root: uv run python apps/mac/scripts/capture-mcp-settings-fixture.py
No owner settings, database, provider, or running engine is accessed.
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from firra.local.readctx import ReadContext
from firra.local.routes.mcp_connect import build_router

with TemporaryDirectory(prefix="firra-mcp-settings-fixture-") as home:
    app = FastAPI()
    app.include_router(build_router(cast(ReadContext, SimpleNamespace(home=Path(home)))))
    with TestClient(app) as client:
        response = client.get("/local/mcp/settings")
        response.raise_for_status()
        enabled = client.put("/local/mcp/settings", json={"processing_tools": True})
        enabled.raise_for_status()
    out = (
        Path(__file__).resolve().parent.parent
        / "Tests/FirraKitTests/Fixtures/LocalRoutes/mcp-settings.json"
    )
    out.write_text(json.dumps(response.json(), indent=2) + "\n")
    out.with_name("mcp-settings-processing-enabled.json").write_text(
        json.dumps(enabled.json(), indent=2) + "\n"
    )
