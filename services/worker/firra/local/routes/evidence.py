"""Timeline, evidence and stored Churn Replay under caller RLS and read audits."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from firra.db import local_evidence as repository
from firra.db.types import WorkspaceId
from firra.local.paging import page, parse_page
from firra.local.readctx import ReadContext
from firra.local.routes.common import audit_read, request_id, workspace_guard
from firra.local.routes.evidence_builders import shape_evidence, shape_replay, shape_run
from firra.local.server import ApiError
from firra_contracts.generated_v1.evidence_item import EvidenceItem
from firra_contracts.generated_v1.evidence_response import EvidenceResponse
from firra_contracts.generated_v1.replay_client_response import ReplayClientResponse
from firra_contracts.generated_v1.replay_runs_response import ReplayRunsResponse
from firra_contracts.generated_v1.timeline_response import TimelineResponse


def build_router(ctx: ReadContext) -> APIRouter:
    def headers(request: Request, response: Response) -> None:
        response.headers["cache-control"] = "no-store"
        response.headers["x-request-id"] = request_id(request)

    router = APIRouter(prefix="/v1/workspaces/{workspace_id}", dependencies=[Depends(headers)])
    Workspace = Annotated[WorkspaceId, Depends(workspace_guard(ctx))]  # noqa: N806

    @router.get("/clients/{client_id}/timeline")
    def timeline(
        workspace_id: Workspace, client_id: UUID, limit: str = "50", cursor: str | None = None
    ) -> Any:
        query = parse_page(limit, cursor)
        if query is None:
            raise ApiError(422, "invalid_request", "Invalid pagination parameters.")
        with ctx.caller() as conn:
            client = repository.visible(conn, workspace_id, client_id)
            if client is None:
                raise ApiError(404, "not_found", "Not found.")
            rows = repository.timeline(
                conn,
                workspace_id,
                client_id,
                query,
                ctx.clock(),
                metadata_only=client["policy"] == "metadata_only",
            )
        items, next_cursor = page(rows, query)
        body = TimelineResponse.model_validate(dict(items=items, next_cursor=next_cursor))
        audit_read(
            ctx, "read.client_timeline", count=len(items), target_type="client", target_id=client_id
        )
        return body.model_dump(mode="json", by_alias=True)

    @router.get("/clients/{client_id}/evidence")
    def client_evidence(
        workspace_id: Workspace, client_id: UUID, limit: str = "50", cursor: str | None = None
    ) -> Any:
        query = parse_page(limit, cursor, evidence=True)
        if query is None:
            raise ApiError(422, "invalid_request", "Invalid pagination parameters.")
        with ctx.caller() as conn:
            client = repository.visible(conn, workspace_id, client_id)
            if client is None:
                raise ApiError(404, "not_found", "Not found.")
            rows = (
                repository.client_evidence(conn, workspace_id, client_id, query, ctx.clock())
                if client["policy"] == "full"
                else []
            )
        items, next_cursor = page(
            [shape_evidence(row, pool=ctx.pool, workspace_id=workspace_id) for row in rows], query
        )
        body = EvidenceResponse.model_validate(dict(items=items, next_cursor=next_cursor))
        audit_read(
            ctx, "read.client_evidence", count=len(items), target_type="client", target_id=client_id
        )
        return body.model_dump(mode="json")

    @router.get("/evidence/{evidence_id}")
    def evidence(workspace_id: Workspace, evidence_id: UUID) -> Any:
        with ctx.caller() as conn:
            row = repository.evidence(conn, workspace_id, evidence_id)
        if row is None:
            raise ApiError(404, "not_found", "Not found.")
        body = EvidenceItem.model_validate(
            shape_evidence(row, pool=ctx.pool, workspace_id=workspace_id)
        )
        audit_read(ctx, "read.evidence", count=1, target_type="evidence", target_id=evidence_id)
        return body.model_dump(mode="json")

    @router.get("/replay/runs")
    def replay_runs(workspace_id: Workspace) -> Any:
        with ctx.caller() as conn:
            items = [shape_run(row) for row in repository.replay_runs(conn, workspace_id)]
        body = ReplayRunsResponse.model_validate(dict(generated_at=ctx.clock(), items=items))
        audit_read(ctx, "read.replay_runs", count=len(items))
        return body.model_dump(mode="json")

    @router.get("/replay/runs/{run_id}/clients/{client_id}")
    def replay_client(workspace_id: Workspace, run_id: UUID, client_id: UUID) -> Any:
        with ctx.caller() as conn:
            client = repository.visible(conn, workspace_id, client_id)
            row = repository.replay_narrative(conn, workspace_id, run_id, client_id)
            if client is None or row is None:
                raise ApiError(404, "not_found", "Not found.")
            body = shape_replay(
                row["narrative"],
                [],
                policy=client["policy"],
                run_id=run_id,
                client_id=client_id,
                now=ctx.clock(),
            )
            if body is None:
                raise ApiError(404, "not_found", "Not found.")
            weeks = repository.replay_weeks(conn, workspace_id, run_id, client_id)
            current = repository.visible(conn, workspace_id, client_id)
            if current is None or current["policy"] != client["policy"]:
                raise ApiError(404, "not_found", "Not found.")
        body["weeks"] = weeks
        body = ReplayClientResponse.model_validate(body).model_dump(mode="json", exclude_unset=True)
        audit_read(ctx, "read.replay_client", count=1, target_type="client", target_id=client_id)
        return body

    return router
