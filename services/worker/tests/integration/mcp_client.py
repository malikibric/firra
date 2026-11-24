"""JSON-RPC helper for the local MCP oracle (L4-04).

Protocol level only: stateless Streamable HTTP — every call is a self-contained
``POST /mcp`` with ``Accept: application/json, text/event-stream`` plus the
bearer token. Parses both plain-JSON and SSE (`data:` line) envelopes.
"""

import json
from typing import Any

STATLESS_ACCEPT = "application/json, text/event-stream"


def _parse_envelope(status: int, content_type: str, text: str) -> Any:
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            if line.startswith("data:"):
                return {"http_status": status, "body": json.loads(line[5:].strip())}
        raise AssertionError(f"SSE response without data line: {text[:200]}")
    return {"http_status": status, "body": json.loads(text) if text.strip() else None}


class McpClient:
    """A bearer-authenticated JSON-RPC peer for ``POST /mcp``."""

    def __init__(self, http: Any, headers: dict[str, str]) -> None:
        self._http = http
        self._headers = {"Accept": STATLESS_ACCEPT, **headers}

    def raw(self, payload: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
        merged = dict(self._headers)
        if headers:
            merged.update(headers)
        response = self._http.post("/mcp", json=payload, headers=merged)
        return _parse_envelope(
            response.status_code, response.headers.get("content-type", ""), response.text
        )

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        return self.raw({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

    def notify(self, method: str, params: dict[str, Any] | None = None) -> Any:
        return self.raw({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def tools_list(self) -> Any:
        return self.call("tools/list")

    def tools_call(self, name: str, arguments: dict[str, Any]) -> Any:
        return self.call("tools/call", {"name": name, "arguments": arguments})

    def prompts_list(self) -> Any:
        return self.call("prompts/list")

    def initialize(self) -> Any:
        return self.call(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "firra-oracle", "version": "1"},
            },
        )


def rpc_result(envelope: Any) -> Any:
    """Unwrap a JSON-RPC success result; raise a readable error otherwise."""
    assert envelope["http_status"] == 200, f"HTTP {envelope['http_status']}: {envelope['body']}"
    body = envelope["body"]
    assert "error" not in body, f"RPC error: {body['error']}"
    return body["result"]


def rpc_error(envelope: Any) -> Any:
    """Unwrap a JSON-RPC error (transport-level 4xx counts as an error envelope too)."""
    if envelope["http_status"] != 200:
        return {"http_status": envelope["http_status"], "body": envelope["body"]}
    assert "error" in envelope["body"], f"expected RPC error, got: {envelope['body']}"
    return envelope["body"]["error"]


def is_error_result(envelope: Any) -> bool:
    """True when a tools/call answered with a tool-level ``isError`` result."""
    body = envelope["body"]
    return (
        envelope["http_status"] == 200
        and isinstance(body, dict)
        and isinstance(body.get("result"), dict)
        and body["result"].get("isError") is True
    )
