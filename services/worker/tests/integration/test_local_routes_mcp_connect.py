"""The connect screen's data: formats are right and nothing secret is in it."""

import base64
import json
import sys
from typing import Any
from urllib.parse import unquote

import pytest

from tests.integration.conftest import LOCAL_AUTH, LOCAL_TOKEN

pytestmark = pytest.mark.integration


def test_connect_items(local_app: tuple[Any, Any, str]) -> None:
    client = local_app[0]
    res = client.get("/local/mcp/connect", headers=LOCAL_AUTH)
    assert res.status_code == 200, res.text
    assert LOCAL_TOKEN not in res.text and "token" not in res.text.lower()
    items = {i["id"]: i for i in res.json()["items"]}
    assert set(items) == {"claude_code", "codex", "cursor", "vscode", "claude_desktop"}
    assert items["claude_code"]["value"].startswith("claude mcp add firra -- ")
    assert "-m firra.local.mcp.stdio" in items["codex"]["value"]
    link = items["cursor"]["value"]
    assert link.startswith("cursor://anysphere.cursor-deeplink/mcp/install?name=firra&config=")
    cursor = json.loads(base64.b64decode(unquote(link.split("config=")[1])))
    assert cursor["command"] == sys.executable and cursor["args"][-1] == "firra.local.mcp.stdio"
    vscode = json.loads(unquote(items["vscode"]["value"].removeprefix("vscode:mcp/install?")))
    assert vscode["name"] == "firra" and vscode["command"] == sys.executable
    desktop = json.loads(items["claude_desktop"]["value"])
    assert desktop["mcpServers"]["firra"]["command"] == sys.executable


def test_switches_default_off_and_persist(local_app: tuple[Any, Any, str]) -> None:
    client = local_app[0]
    off = {"source_access": False, "processing_tools": False}
    assert client.get("/local/mcp/settings", headers=LOCAL_AUTH).json() == off
    on = client.put("/local/mcp/settings", json={"source_access": True}, headers=LOCAL_AUTH)
    assert on.status_code == 200 and on.json() == {**off, "source_access": True}
    both = client.put(
        "/local/mcp/settings",
        json={"source_access": False, "processing_tools": True},
        headers=LOCAL_AUTH,
    )
    assert both.json() == {"source_access": False, "processing_tools": True}
    assert client.get("/local/mcp/settings", headers=LOCAL_AUTH).json() == both.json()
    for bad in ({"source_access": "yes"}, {"processing_tools": 1}, {}, {"other": True}):
        res = client.put("/local/mcp/settings", json=bad, headers=LOCAL_AUTH)
        assert res.status_code == 422, bad
    assert client.get("/local/mcp/settings", headers=LOCAL_AUTH).json() == both.json()
    client.put("/local/mcp/settings", json=off, headers=LOCAL_AUTH)


def test_engine_catalog_has_no_processing_tools_by_default(
    local_app: tuple[Any, Any, str],
) -> None:
    from tests.integration.mcp_client import McpClient, rpc_result

    mcp = McpClient(local_app[0], LOCAL_AUTH)
    names = {tool["name"] for tool in rpc_result(mcp.tools_list())["tools"]}
    assert len(names) == 22 and not names & {"get_pending_mail", "submit_extractions"}
    prompts = {p["name"] for p in rpc_result(mcp.prompts_list())["prompts"]}
    assert "process_new_mail" not in prompts
