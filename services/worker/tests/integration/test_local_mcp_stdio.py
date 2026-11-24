"""A real SDK stdio subprocess forwards to a live, authenticated engine HTTP app."""

import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import anyio
import pytest
import uvicorn
from mcp import Client, StdioServerParameters
from mcp.types import TextContent

from firra.local.routes import all_routers
from firra.local.server import create_local_app
from firra.local.settings import OwnerSettings, save
from firra_contracts import EngineStatus

MCP_PROCESSING_TOOLS = True
pytestmark = pytest.mark.integration


async def check_stdio(home: Path, *, offline: bool, processing_tools: bool = True) -> None:
    save(OwnerSettings(mcp_processing_tools=processing_tools), home)
    async with Client(
        StdioServerParameters(
            command=str(Path(sys.executable).with_name("firra-mcp")),
            args=[],
            env={"FIRRA_HOME": str(home)},
        )
    ) as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == (24 if processing_tools else 22)
        if not offline:
            for tool in tools:
                print(tool.name, json.dumps(tool.input_schema, sort_keys=True))  # noqa: T201 - brief acceptance
        names = [t.name for t in tools] if offline else ["list_clients"]
        for name in names:
            args: dict[str, Any] = {}
            if name in (
                "get_client_context",
                "get_client_state",
                "get_risks",
                "get_commitments",
                "get_stakeholders",
                "get_client_unknowns",
                "get_client_timeline",
                "search_evidence",
                "prepare_for_client",
            ):
                args["client_id"] = "00000000-0000-4000-8000-000000000001"
            elif name == "get_evidence":
                args["evidence_id"] = "00000000-0000-4000-8000-000000000001"
            elif name == "record_feedback":
                args = {
                    "card_id": "00000000-0000-4000-8000-000000000001",
                    "verdict": "useful",
                    "user_confirmed": True,
                }
            elif name == "record_outcome":
                args = {
                    "client_id": "00000000-0000-4000-8000-000000000001",
                    "event": "renewed",
                    "occurred_at": "2026-10-01",
                    "user_confirmed": True,
                }
            elif name == "search_client_sources":
                args = {"client": "Acme", "query": "price"}
            elif name == "get_source_context":
                args = {"ref": "src_" + "0" * 32}
            elif name == "refresh_client_sources":
                args = {"client": "Acme"}
            elif name == "submit_extractions":
                args = {
                    "lease_id": "00000000-0000-4000-8000-000000000001",
                    "output": {"results": []},
                }
            result = await client.call_tool(name, args)
            if offline:
                assert result.is_error
                assert isinstance(result.content[0], TextContent)
                assert result.content[0].text == "Firra is not running", name
            else:
                assert not result.is_error
                assert result.structured_content is not None


def test_stdio_offline_catalog_and_every_call(tmp_path: Path) -> None:
    anyio.run(lambda: check_stdio(tmp_path, offline=True))


def test_stdio_default_catalog_has_no_processing_tools(tmp_path: Path) -> None:
    async def run() -> None:
        async with Client(
            StdioServerParameters(
                command=str(Path(sys.executable).with_name("firra-mcp")),
                args=[],
                env={"FIRRA_HOME": str(tmp_path)},
            )
        ) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert len(names) == 22
            assert not names & {"get_pending_mail", "submit_extractions"}
            assert "process_new_mail" not in {p.name for p in (await client.list_prompts()).prompts}

    anyio.run(run)


def test_stdio_forwards_to_live_engine(local_app: tuple[Any, Any, str], tmp_path: Path) -> None:
    http, ctx, _ = local_app
    status = EngineStatus.model_validate(
        http.get("/local/status", headers={"Authorization": "Bearer " + "t" * 40}).json()
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = create_local_app(
        api_token="t" * 40, port=port, status=lambda: status, routers=all_routers(ctx)
    )
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        run = tmp_path / "run"
        run.mkdir()
        path = run / "engine.json"
        path.write_text(json.dumps({"port": port, "token": "t" * 40}))
        path.chmod(0o600)
        anyio.run(lambda: check_stdio(tmp_path, offline=False))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
