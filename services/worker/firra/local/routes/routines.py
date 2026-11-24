"""Routine switches: reference port of the read route (GET) and the write store (PUT)."""

from typing import Annotated, Any

import psycopg.errors
from fastapi import APIRouter, Depends

from firra.db.local_reads import routines as routine_rows
from firra.db.types import WorkspaceId
from firra.local.readctx import ReadContext
from firra.local.routes.common import audit_read, workspace_guard
from firra.local.server import ApiError
from firra_contracts.generated_v1.mutation_response import MutationResponse
from firra_contracts.generated_v1.routine_enabled_request import RoutineEnabledRequest
from firra_contracts.generated_v1.routines_response import RoutinesResponse

KINDS = ("morning_scan", "commitment_watch", "pre_meeting_brief")


def build_router(ctx: ReadContext) -> APIRouter:
    router = APIRouter(prefix="/v1/workspaces/{workspace_id}")
    Workspace = Annotated[WorkspaceId, Depends(workspace_guard(ctx))]  # noqa: N806 - a type alias

    @router.get("/routines")
    def list_routines(workspace_id: Workspace) -> Any:
        with ctx.caller() as conn:
            rows = routine_rows(conn, workspace_id)
        by_kind = {r["kind"]: r for r in rows}
        items = []
        for kind in KINDS:
            row = by_kind.get(kind)
            last = row["last_run"] if row else None
            items.append(
                {
                    "kind": kind,
                    "enabled": row["enabled"] if row else True,
                    "created": row is not None,
                    "last_run": last,
                }
            )
        body = RoutinesResponse.model_validate(
            {"generated_at": ctx.clock().isoformat(), "items": items}
        )
        audit_read(ctx, "read.routines", count=len(items))
        return body.model_dump(mode="json")

    @router.put("/routines/{kind}")
    def set_routine(kind: str, request: RoutineEnabledRequest, workspace_id: Workspace) -> Any:
        if kind not in KINDS:
            raise ApiError(422, "invalid_request", "Invalid routine switch.")
        try:
            with ctx.caller() as conn:
                row = conn.execute(
                    "select public.set_routine_enabled(%s, %s, %s) as id",
                    (workspace_id, kind, request.enabled),
                ).fetchone()
        except psycopg.errors.InsufficientPrivilege:
            raise ApiError(404, "not_found", "Not found.") from None
        if row is None:
            raise ApiError(500, "internal_error", "The request could not be completed.")
        return MutationResponse.model_validate({"id": str(row["id"])}).model_dump(mode="json")

    return router
