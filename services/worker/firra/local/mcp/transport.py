"""Stateless SDK HTTP mounted behind the engine's bearer/Host/Origin guards."""

from typing import Any

from fastapi import APIRouter
from mcp.server.mcpserver import MCPServer
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from firra.local.mcp.request_scope import REQUEST_SCOPES


class HttpEndpoint:
    def __init__(self, server: MCPServer[Any]) -> None:
        self.server = server

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Each stateless transport owns its task group for this request, including when
        # the parent TestClient has not entered a lifespan (the independent oracle).
        header = dict(scope.get("headers") or []).get(b"x-firra-scopes")
        scopes = frozenset(header.decode("ascii", "ignore").split(",")) if header else None
        token = REQUEST_SCOPES.set(scopes)
        try:
            await self._serve(scope, receive, send)
        finally:
            REQUEST_SCOPES.reset(token)

    async def _serve(self, scope: Scope, receive: Receive, send: Send) -> None:
        app = self.server.streamable_http_app(
            stateless_http=True, json_response=True, max_request_body_size=2 * 1024 * 1024
        )
        async with app.router.lifespan_context(app):
            await app(scope, receive, send)


def http_router(server: MCPServer[Any]) -> APIRouter:
    router = APIRouter()
    router.routes.append(Route("/mcp", endpoint=HttpEndpoint(server), methods=["POST"]))
    return router
