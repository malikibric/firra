"""Owner-AI controls; no content, credentials or executor output crosses these routes."""

import json
from typing import Any

from fastapi import APIRouter
from pydantic import ValidationError

from firra.ai.local import AiRuntime
from firra.local.server import ApiError
from firra_contracts import AiSettings


def build_router(runtime: AiRuntime) -> APIRouter:
    router = APIRouter(prefix="/local/ai")

    @router.get("/settings")
    def settings() -> Any:
        return runtime.settings().model_dump(mode="json", exclude_none=True)

    @router.put("/settings")
    def update(body: dict[str, Any]) -> Any:
        try:
            settings = AiSettings.model_validate_json(json.dumps(body), strict=True)
        except ValidationError:
            raise ApiError(422, "invalid_request", "Invalid AI settings.") from None
        # Optional fields may be omitted, but explicit null is not in their wire schema.
        if any(body.get(k, False) is None for k in ("batch_records", "background_acknowledged")):
            raise ApiError(422, "invalid_request", "Invalid AI settings.")
        try:
            runtime.update(settings)
        except ValueError as exc:
            code = str(exc)
            message = (
                "Confirm background processing first."
                if code == "background_acknowledgement_required"
                else "Codex background processing is not available in this build."
            )
            raise ApiError(422, code, message) from None
        return runtime.settings().model_dump(mode="json", exclude_none=True)

    @router.get("/status")
    def status() -> Any:
        result = runtime.status() if runtime.tools else runtime.probe()
        return result.model_dump(mode="json", exclude_none=True)

    @router.post("/probe")
    def probe() -> Any:
        return runtime.probe().model_dump(mode="json", exclude_none=True)

    return router
