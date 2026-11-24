"""``GET /local/mcp/connect``: how to add Firra's local MCP server to the owner's AI tools.

The server is the stdio proxy of the engine's Python (``python -I -B -m firra.local.mcp.stdio``);
it finds the engine through the 0600 token file, so nothing here is secret. Install formats come
from docs/research/mcp-clients.md (``claude mcp add``, ``codex mcp add``, Cursor and VS Code install
links); the Claude Desktop entry is a config snippet until the .mcpb package exists.
"""

import base64
import json
import shlex
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter

from firra.local.readctx import ReadContext
from firra.local.server import ApiError
from firra.local.settings import engine_home, load, save
from firra_contracts.generated.local.mcp_connect import McpConnect
from firra_contracts.generated.local.mcp_settings import McpSettings

NAME = "firra"
ARGS = ["-I", "-B", "-m", "firra.local.mcp.stdio"]


def build_items(python: str, home: Path | None) -> list[dict[str, Any]]:
    server: dict[str, Any] = {"command": python, "args": ARGS}
    if home is not None and home != engine_home():
        server["env"] = {"FIRRA_HOME": str(home)}
    command = " ".join(shlex.quote(p) for p in [python, *ARGS])
    cursor_config = base64.b64encode(json.dumps(server).encode()).decode()
    return [
        {
            "id": "claude_code",
            "title": "Claude Code",
            "kind": "command",
            "value": f"claude mcp add {NAME} -- {command}",
            "note": "Run in a terminal, then start Claude Code and ask about your clients.",
        },
        {
            "id": "codex",
            "title": "Codex",
            "kind": "command",
            "value": f"codex mcp add {NAME} -- {command}",
            "note": None,
        },
        {
            "id": "cursor",
            "title": "Cursor",
            "kind": "deeplink",
            "value": "cursor://anysphere.cursor-deeplink/mcp/install"
            f"?name={NAME}&config={cursor_config}",
            "note": None,
        },
        {
            "id": "vscode",
            "title": "VS Code",
            "kind": "deeplink",
            "value": "vscode:mcp/install?" + quote(json.dumps({"name": NAME, **server})),
            "note": None,
        },
        {
            "id": "claude_desktop",
            "title": "Claude Desktop",
            "kind": "config",
            "value": json.dumps({"mcpServers": {NAME: server}}, indent=2),
            "note": "Add to Claude Desktop config (Settings, Developer, Edit Config), restart.",
        },
    ]


def build_router(ctx: ReadContext) -> APIRouter:
    router = APIRouter(prefix="/local/mcp")

    @router.get("/connect")
    def connect() -> Any:
        body = McpConnect.model_validate(
            {"server_name": NAME, "items": build_items(sys.executable, ctx.home)}
        )
        return body.model_dump(mode="json")

    def current() -> Any:
        owner = load(ctx.home)
        return McpSettings(
            source_access=owner.mcp_source_access, processing_tools=owner.mcp_processing_tools
        ).model_dump(mode="json")

    @router.get("/settings")
    def settings() -> Any:
        return current()

    @router.put("/settings")
    def update_settings(body: dict[str, Any]) -> Any:
        # Privacy switches are real booleans: "yes" or 1 are refused, not coerced.
        fields = {"source_access": "mcp_source_access", "processing_tools": "mcp_processing_tools"}
        if not body or set(body) - set(fields) or not all(type(v) is bool for v in body.values()):
            raise ApiError(
                422, "invalid_request", "Send source_access and/or processing_tools as booleans."
            )
        save(load(ctx.home).model_copy(update={fields[k]: v for k, v in body.items()}), ctx.home)
        return current()

    return router
