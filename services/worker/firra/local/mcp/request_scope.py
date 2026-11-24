"""Scopes of the MCP request in progress when it came through a connector link (None = local)."""

from contextvars import ContextVar

REQUEST_SCOPES: ContextVar[frozenset[str] | None] = ContextVar("request_scopes", default=None)
