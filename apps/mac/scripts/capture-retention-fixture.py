"""Capture default GET /local/retention from the real route using a scratch home.

Run from the repository root: uv run python apps/mac/scripts/capture-retention-fixture.py
No owner settings, database, running engine, or provider is accessed.
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from firra.local.readctx import ReadContext
from firra.local.routes.maintenance import build_router

with TemporaryDirectory(prefix="firra-retention-fixture-") as home:
    ctx = SimpleNamespace(home=Path(home), identity=SimpleNamespace(workspace_id=UUID(int=1)))
    app = FastAPI()
    app.include_router(build_router(cast(ReadContext, ctx)))
    with TestClient(app) as client:
        response = client.get("/local/retention")
        response.raise_for_status()
    out = (
        Path(__file__).resolve().parent.parent
        / "Tests/FirraKitTests/Fixtures/LocalRoutes/retention.json"
    )
    out.write_text(json.dumps(response.json(), indent=2) + "\n")
