"""Client read routes: generated contracts, authenticated RLS and fail-closed read audit."""

import re
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from firra.db import local_clients as repository
from firra.db.types import WorkspaceId
from firra.local.readctx import ReadContext
from firra.local.routes.client_builders import (
    build_attention,
    build_changes,
    build_client_detail,
    build_client_list_item,
)
from firra.local.routes.common import audit_read, request_id, workspace_guard
from firra.local.routes.meeting_builder import build_meetings
from firra.local.server import ApiError
from firra_contracts.generated_v1.attention_response import AttentionResponse
from firra_contracts.generated_v1.audit_response import AuditResponse
from firra_contracts.generated_v1.changes_response import ChangesResponse
from firra_contracts.generated_v1.client_detail_response import ClientDetailResponse
from firra_contracts.generated_v1.clients_response import ClientsResponse
from firra_contracts.generated_v1.meetings_response import MeetingsResponse
from firra_contracts.generated_v1.setup_clients_response import SetupClientsResponse


def now_of(ctx: ReadContext) -> Any:
    return ctx.clock()


def build_router(ctx: ReadContext) -> APIRouter:
    def read_headers(request: Request, response: Response) -> None:
        response.headers["cache-control"] = "no-store"
        response.headers["x-request-id"] = request_id(request)

    router = APIRouter(prefix="/v1/workspaces/{workspace_id}", dependencies=[Depends(read_headers)])
    Workspace = Annotated[WorkspaceId, Depends(workspace_guard(ctx))]  # noqa: N806

    @router.get("/attention")
    def attention(workspace_id: Workspace) -> Any:
        now = ctx.clock()
        with ctx.caller() as conn:
            items = build_attention(
                repository.attention(conn, workspace_id, now),
                repository.visible_clients(conn, workspace_id),
                now,
            )
        body = AttentionResponse.model_validate(dict(generated_at=now, items=items))
        audit_read(ctx, "read.attention", count=len(items))
        return body.model_dump(mode="json")

    @router.get("/changes")
    def changes(workspace_id: Workspace, since: str | None = None) -> Any:
        now = ctx.clock()
        day = since if since is not None else (now - timedelta(days=7)).date().isoformat()
        oldest = (now - timedelta(days=31)).date().isoformat()
        if (
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)
            or day < oldest
            or day > now.date().isoformat()
        ):
            raise ApiError(
                422, "invalid_request", "Since must be a valid date within the last 31 days."
            )
        with ctx.caller() as conn:
            items = build_changes(
                repository.changes_assessments(conn, workspace_id, day, now),
                repository.changes_events(conn, workspace_id, day, now),
                repository.visible_clients(conn, workspace_id),
                day,
                now,
            )
        body = ChangesResponse.model_validate(dict(generated_at=now, since=day, items=items))
        audit_read(ctx, "read.changes", count=len(items))
        return body.model_dump(mode="json")

    @router.get("/clients")
    def clients(workspace_id: Workspace, status: str = "active") -> Any:
        if status not in ("active", "churned", "all"):
            raise ApiError(422, "invalid_request", "Status must be active, churned or all.")
        with ctx.caller() as conn:
            rows = repository.clients(conn, workspace_id, status)
        items = [build_client_list_item(r, r["assessment"]) for r in rows]
        body = ClientsResponse.model_validate(dict(generated_at=now_of(ctx), items=items))
        audit_read(ctx, "read.clients", count=len(items))
        return body.model_dump(mode="json")

    @router.get("/meetings/upcoming")
    def meetings(workspace_id: Workspace) -> Any:
        now = ctx.clock()
        with ctx.caller() as conn:
            briefs, events, clients = repository.meetings(conn, workspace_id, now)
        items = build_meetings(briefs, events, clients, now)
        body = MeetingsResponse.model_validate(dict(generated_at=now, items=items))
        audit_read(ctx, "read.meeting_briefs", count=len(items))
        return body.model_dump(mode="json")

    @router.get("/clients/{client_id}")
    def detail(workspace_id: Workspace, client_id: UUID) -> Any:
        with ctx.caller() as conn:
            row = repository.client(conn, workspace_id, client_id)
            if row is None:
                raise ApiError(404, "not_found", "Not found.")
            assessment, history = repository.detail_assessments(conn, workspace_id, client_id)
            parts = repository.detail_parts(
                conn, workspace_id, client_id, metadata_only=row["policy"] == "metadata_only"
            )
        body = ClientDetailResponse.model_validate(
            dict(
                generated_at=ctx.clock(),
                client=build_client_detail(row, assessment, history, parts),
            )
        )
        audit_read(ctx, "read.client_detail", count=1, target_type="client", target_id=client_id)
        return body.model_dump(mode="json", by_alias=True, exclude_unset=True)

    @router.get("/setup/clients")
    def setup_clients(workspace_id: Workspace) -> Any:
        with ctx.caller() as conn:
            items = repository.setup_clients(conn, workspace_id)
        body = SetupClientsResponse.model_validate(dict(items=items))
        audit_read(ctx, "read.setup_clients", count=len(items))
        return body.model_dump(mode="json")

    @router.get("/audit")
    def audit(workspace_id: Workspace, page: str = "1") -> Any:
        if not re.fullmatch(r"[1-9]\d{0,2}", page) or int(page) > 200:
            raise ApiError(422, "invalid_request", "Invalid audit page.")
        number = int(page)
        with ctx.caller() as conn:
            items = repository.audit(conn, workspace_id, number)
        body = AuditResponse.model_validate(
            dict(
                items=items[:50], next_page=number + 1 if len(items) > 50 and number < 200 else None
            )
        )
        audit_read(ctx, "read.setup_audit", count=len(body.items))
        return body.model_dump(mode="json")

    return router
