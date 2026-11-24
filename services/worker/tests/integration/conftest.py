"""A session-private bundled cluster for engine integration tests, without Docker."""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from firra.local.pg import PostgresSupervisor

PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")


@pytest.fixture(scope="session", autouse=True)
def local_database() -> Iterator[None]:
    if not PG_BIN or os.environ.get("FIRRA_DATABASE_URL"):
        yield
        return
    with tempfile.TemporaryDirectory(prefix="firra-integration-", dir="/tmp") as root:
        pg = PostgresSupervisor(Path(PG_BIN), Path(root) / "data", Path(root) / "run")
        try:
            os.environ["FIRRA_DATABASE_URL"] = pg.ensure_ready()
            yield
        finally:
            os.environ.pop("FIRRA_DATABASE_URL", None)
            pg.stop()


# --- the local /v1 app on its own bundled cluster, one per test module ---------------------------

LOCAL_TOKEN = "t" * 40
LOCAL_PORT = 49152
LOCAL_AUTH = {"Authorization": f"Bearer {LOCAL_TOKEN}"}


@pytest.fixture(scope="module")
def local_app(request: pytest.FixtureRequest) -> Iterator[tuple[Any, Any, str]]:
    """``(TestClient, ReadContext, conninfo)`` for the local routes; skipped without the bundle.

    A test module that needs the MCP processing tools sets ``MCP_PROCESSING_TOOLS = True``
    (the owner setting is off by default, so the catalog normally lacks them).
    """
    if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    from fastapi.testclient import TestClient

    from firra.db.pool import create_pool_for
    from firra.local.bootstrap import ensure_local_owner
    from firra.local.readctx import ReadContext
    from firra.local.routes import all_routers
    from firra.local.server import create_local_app
    from firra_contracts import EngineStatus

    root = Path(tempfile.mkdtemp(prefix="firra-app-", dir="/tmp"))
    pg = PostgresSupervisor(Path(PG_BIN), root / "data", root / "run")
    try:
        conninfo = pg.ensure_ready()
        identity = ensure_local_owner(conninfo, timezone="UTC")
        status = EngineStatus.model_validate(
            {
                "engine_version": "t",
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
        with create_pool_for(conninfo, min_size=1, max_size=4) as pool:
            home = root / "home"
            home.mkdir(mode=0o700)
            if getattr(request.module, "MCP_PROCESSING_TOOLS", False):
                from firra.local.settings import OwnerSettings, save

                save(OwnerSettings(mcp_processing_tools=True), home)
            # Never the owner's real Application Support: settings writes go to the scratch home.
            ctx = ReadContext(pool=pool, identity=identity, home=home)
            app = create_local_app(
                api_token=LOCAL_TOKEN,
                port=LOCAL_PORT,
                status=lambda: status,
                routers=all_routers(ctx),
            )
            yield TestClient(app, base_url=f"http://127.0.0.1:{LOCAL_PORT}"), ctx, conninfo
    finally:
        pg.stop()
        import shutil

        shutil.rmtree(root, ignore_errors=True)
