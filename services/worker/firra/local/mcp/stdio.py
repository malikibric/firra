"""Stdio catalog whose calls forward to the authenticated, loopback-only engine."""

import asyncio
import json
import os
import stat
from typing import Any

import httpx
from mcp.types import CallToolResult

from firra.local.mcp.server import build_server, error
from firra.local.settings import engine_home, load


def forward(name: str, args: dict[str, Any]) -> CallToolResult:
    try:
        path = engine_home() / "run" / "engine.json"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r") as handle:
            info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()
            ):
                return error("Firra is not running")
            endpoint = json.load(handle)
        port, token = endpoint["port"], endpoint["token"]
        if (
            type(port) is not int
            or not 1 <= port <= 65535
            or not isinstance(token, str)
            or len(token) < 32
        ):
            return error("Firra is not running")
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": json.loads(json.dumps(args, default=str))},
        }
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
            response = client.post(
                f"http://127.0.0.1:{port}/mcp",
                json=payload,
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/json, text/event-stream",
                },
            )
            response.raise_for_status()
            value = response.json()
        if "result" not in value:
            return error()
        return CallToolResult.model_validate(value["result"])
    except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
        return error("Firra is not running")


def main() -> None:
    server = build_server(forward, processing_tools=load().mcp_processing_tools)
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
