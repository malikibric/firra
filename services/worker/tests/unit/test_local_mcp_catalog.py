"""SDK catalog schemas, confirmation and sanitisation before the handler is invoked."""

from pathlib import Path
from typing import Any
from uuid import uuid4

import anyio
import pytest
from mcp import Client
from mcp.types import TextContent

from firra.local.mcp.server import RULES, UNTRUSTED_NOTE, build_server, error

PROCESSING = {"get_pending_mail", "submit_extractions"}


def test_default_catalog_has_no_processing_tools() -> None:
    async def run() -> None:
        async with Client(build_server(lambda name, args: error())) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert len(names) == 22 and not names & PROCESSING
            assert "process_new_mail" not in {p.name for p in (await client.list_prompts()).prompts}
            result = await client.call_tool("get_pending_mail", {})
            assert result.is_error

    anyio.run(run)


def test_processing_packet_limit_is_five_by_default_and_ten_at_most() -> None:
    seen: list[dict[str, Any]] = []

    def handler(name: str, args: dict[str, Any]) -> Any:
        seen.append(args)
        return error()

    async def run() -> None:
        async with Client(build_server(handler, processing_tools=True)) as client:
            tool = next(
                t for t in (await client.list_tools()).tools if t.name == "get_pending_mail"
            )
            assert tool.input_schema["properties"]["limit"]["default"] == 5
            assert tool.input_schema["properties"]["limit"]["maximum"] == 10
            for limit in (11, 20, 0):
                assert (await client.call_tool("get_pending_mail", {"limit": limit})).is_error
            assert seen == []
            await client.call_tool("get_pending_mail", {})
            await client.call_tool("get_pending_mail", {"limit": 10})
            assert seen == [{"limit": 5}, {"limit": 10}]

    anyio.run(run)


def test_catalog_and_output_schemas() -> None:
    async def run() -> None:
        async with Client(
            build_server(lambda name, args: error(), processing_tools=True)
        ) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 24
            for tool in tools:
                assert tool.description is not None
                assert RULES in tool.description and UNTRUSTED_NOTE in tool.description
                assert tool.input_schema["additionalProperties"] is False
                if tool.name not in ("get_pending_mail", "submit_extractions"):
                    assert tool.output_schema is not None
            prompts = {p.name: p for p in (await client.list_prompts()).prompts}
            text = (await client.get_prompt("process_new_mail")).messages[0].content
            assert "exactly ONE packet" in text.text and "ask the owner" in text.text  # type: ignore[union-attr]
            assert list(prompts) == [
                "morning_review",
                "prepare_for_meeting",
                "weekly_review",
                "process_new_mail",
                "draft_reply",
            ]

    anyio.run(run)


@pytest.mark.parametrize("confirmed", [None, False, 1, "true"])
def test_confirmation_is_literal_boolean_before_call(confirmed: Any) -> None:
    seen = []

    def handler(name: str, args: dict[str, Any]) -> Any:
        seen.append(name)
        return error()

    async def run() -> None:
        async with Client(build_server(handler)) as client:
            args = {"card_id": str(uuid4()), "verdict": "useful"}
            if confirmed is not None:
                args["user_confirmed"] = confirmed
            result = await client.call_tool("record_feedback", args)
            assert result.is_error
            assert isinstance(result.content[0], TextContent)
            assert "card_id" not in result.content[0].text

    anyio.run(run)
    assert seen == []


def test_sdk_validation_and_unknown_tool_are_sanitised() -> None:
    async def run() -> None:
        async with Client(build_server(lambda name, args: error())) as client:
            for name, args in [
                ("get_client_context", {"client_id": "private@example.test"}),
                ("unknown-private@example.test", {}),
                ("list_clients", {"private": "secret"}),
            ]:
                result = await client.call_tool(name, args)
                assert result.is_error
                assert isinstance(result.content[0], TextContent)
                assert result.content[0].text == "Firra could not complete this request."

    anyio.run(run)


def test_processing_tools_setting_is_off_by_default_and_persists(tmp_path: Path) -> None:
    from firra.local.settings import OwnerSettings, load, save

    assert OwnerSettings().mcp_processing_tools is False
    assert load(tmp_path).mcp_processing_tools is False
    save(OwnerSettings(mcp_processing_tools=True), tmp_path)
    assert load(tmp_path).mcp_processing_tools is True
