"""``GET /v1/me``: the local owner and the one workspace (contributors do not exist in Local v1)."""

from typing import Any

from fastapi import APIRouter

from firra.db.local_reads import memberships
from firra.local.readctx import ReadContext
from firra_contracts.generated_v1.me_response import MeResponse


def build_router(ctx: ReadContext) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/me")
    def me() -> Any:
        with ctx.caller() as conn:
            rows = memberships(conn, ctx.identity.user_id)
        body = MeResponse.model_validate(
            {
                "user": {"id": str(ctx.identity.user_id), "email": ctx.identity.email},
                "memberships": [
                    {
                        "workspace_id": str(r["workspace_id"]),
                        "workspace_name": r["workspace_name"],
                        "role": r["role"],
                    }
                    for r in rows
                ],
                "contributions": [],
            }
        )
        return body.model_dump(mode="json")

    return router
