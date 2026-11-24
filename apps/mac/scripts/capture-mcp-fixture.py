"""Capture GET /local/mcp/connect from the real route; no database or provider calls.

Run from the repository root: uv run python apps/mac/scripts/capture-mcp-fixture.py
The route only reads ctx.home; a synthetic context keeps this capture independent of owner data.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from firra.local.readctx import ReadContext
from firra.local.routes.mcp_connect import build_router

app = FastAPI()
app.include_router(build_router(cast(ReadContext, SimpleNamespace(home=None))))
with TestClient(app) as client:
    response = client.get("/local/mcp/connect")
    response.raise_for_status()
out = (
    Path(__file__).resolve().parent.parent
    / "Tests/FirraKitTests/Fixtures/LocalRoutes/mcp-connect.json"
)
out.write_text(json.dumps(response.json(), indent=2) + "\n")
