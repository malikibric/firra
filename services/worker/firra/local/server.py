"""The engine's local HTTP API frame (docs/plans/local/contracts.md section 3).

Loopback only, one bearer token per engine start, no browser access: a request that carries an
``Origin`` header (every browser request does, native clients send none), a ``Host`` other than the
loopback address and port, a body over 2 MiB or a wrong token is refused before any route runs.
Errors use the ``/v1`` error model ``{error, message, request_id}``. Messages never hold content.
"""

import hmac
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from firra.logging import get_logger
from firra_contracts import EngineStatus

log = get_logger(__name__)

MAX_BODY_BYTES = 2 * 1024 * 1024
_BEARER = re.compile(r"^Bearer (\S+)$")


class ApiError(Exception):
    """Raised by routes; becomes ``{error, message, request_id}``."""

    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message or code.replace("_", " ")


def _failure(status: int, code: str, message: str, request_id: str) -> JSONResponse:
    return JSONResponse(
        {"error": code, "message": message, "request_id": request_id}, status_code=status
    )


def create_local_app(
    *,
    api_token: str,
    port: int,
    status: Callable[[], EngineStatus],
    routers: Sequence[APIRouter] = (),
) -> FastAPI:
    """Builds the app; ``routers`` carry the read models and writes (``firra.local.routes``)."""
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    expected = api_token.encode()

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = uuid4().hex
        request.state.request_id = request_id
        headers = request.headers
        if "origin" in headers:
            return _failure(403, "forbidden_origin", "browser requests are refused", request_id)
        if headers.get("host", "") not in hosts:
            return _failure(403, "bad_host", "unexpected host", request_id)
        match = _BEARER.match(headers.get("authorization", ""))
        if match is None or not hmac.compare_digest(match.group(1).encode(), expected):
            return _failure(401, "unauthorized", "missing or wrong token", request_id)
        length = headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
            return _failure(413, "payload_too_large", "body too large", request_id)
        try:
            return await call_next(request)
        except ApiError as exc:
            return _failure(exc.status, exc.code, exc.message, request_id)
        except Exception as exc:
            log.error(
                "local.api.unhandled",
                error_class=type(exc).__name__,
                sqlstate=getattr(exc, "sqlstate", None),
            )
            return _failure(500, "internal_error", "internal error", request_id)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, _exc: RequestValidationError) -> JSONResponse:
        # Never echo the offending input: it can hold client text.
        return _failure(422, "invalid_request", "invalid request", request.state.request_id)

    @app.get("/local/status")
    def engine_status() -> Any:
        return status().model_dump(mode="json")

    for router in routers:
        app.include_router(router)
    return app
