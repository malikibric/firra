"""Decisions list, feedback and outcomes: port of the read route and ``write-store.ts``."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import psycopg.errors
from fastapi import APIRouter, Depends, Query, Response

from firra.db.local_decisions import (
    card_visible,
    client_visible,
    decision_rows,
    outcome_visible,
)
from firra.db.types import WorkspaceId
from firra.local.paging import instant, page, parse_page
from firra.local.readctx import ReadContext
from firra.local.routes.common import audit_read, workspace_guard
from firra.local.server import ApiError
from firra_contracts.generated_v1.decisions_response import DecisionsResponse
from firra_contracts.generated_v1.feedback_request import FeedbackRequest
from firra_contracts.generated_v1.mutation_response import MutationResponse
from firra_contracts.generated_v1.outcome_request import OutcomeRequest

STATUSES = ("new", "done", "dismissed", "all")
IMPORTANCES = ("fyi", "attention", "important")
KINDS = (
    "risk",
    "margin",
    "pricing",
    "growth",
    "external",
    "commitment",
    "state_change",
    "commitment_watch",
    "expansion",
)
_INVALID = ApiError(422, "invalid_request", "Invalid list parameters.")


def _check_outcome(body: OutcomeRequest) -> None:
    """The two refinements of the zod contract that JSON Schema cannot carry."""
    if (_text(body.amount_change) is None) != (_text(body.currency) is None):
        raise ApiError(422, "invalid_request", "Invalid outcome body.")
    occurred = _text(body.occurred_at)
    for earlier in (_text(body.notice_at), _text(body.first_known_at)):
        if earlier is not None and earlier > occurred:
            raise ApiError(422, "invalid_request", "Invalid outcome body.")


def _text(value: Any) -> Any:
    """Plain Python value of a generated enum or root model (``Note``, ``Currency`` ...)."""
    value = getattr(value, "root", value)
    return getattr(value, "value", value)


def build_router(ctx: ReadContext) -> APIRouter:
    router = APIRouter(prefix="/v1/workspaces/{workspace_id}")
    Workspace = Annotated[WorkspaceId, Depends(workspace_guard(ctx))]  # noqa: N806 - a type alias

    @router.get("/decisions")
    def list_decisions(
        workspace_id: Workspace,
        status: str = "new",
        limit: str = "50",
        cursor: str | None = None,
        importance: Annotated[str | None, Query()] = None,
        kind: Annotated[str | None, Query()] = None,
    ) -> Any:
        query = parse_page(limit, cursor, evidence=True)
        if (
            query is None
            or status not in STATUSES
            or (importance is not None and importance not in IMPORTANCES)
            or (kind is not None and kind not in KINDS)
        ):
            raise _INVALID
        after = (
            (
                datetime.fromisoformat(query.cursor.occurred_at.replace("Z", "+00:00")),
                UUID(query.cursor.id),
            )
            if query.cursor
            else None
        )
        with ctx.caller() as conn:
            rows = decision_rows(
                conn,
                workspace_id=workspace_id,
                user_id=ctx.identity.user_id,
                status=None if status == "all" else status,
                importance=importance,
                kind=kind,
                cursor=after,
                limit=query.limit + 1,
            )
        for row in rows:
            facts = row["facts"] or []
            if row["client_policy"] == "metadata_only" and (
                not all(isinstance(f, dict) and f.get("basis") == "metadata" for f in facts)
                or row["evidence_ids"]
            ):
                raise ApiError(500, "internal_error", "The request could not be completed.")
        items, next_cursor = page(
            [{**r, "occurred_at": r["created_at"]} for r in rows],
            query,
        )
        body = DecisionsResponse.model_validate(
            {
                "items": [
                    {
                        **{
                            k: _text(r[k])
                            for k in (
                                "id",
                                "client_id",
                                "client_name",
                                "kind",
                                "title",
                                "body",
                                "play",
                                "strength",
                                "priority",
                                "status",
                            )
                        },
                        "week": r["week"].isoformat(),
                        "importance": r["importance"],
                        "surfaced_on": r["surfaced_on"].isoformat() if r["surfaced_on"] else None,
                        "created_at": instant(r["created_at"]),
                        "evidence_ids": [str(e) for e in r["evidence_ids"]],
                        "feedback": r["feedback"],
                    }
                    for r in items
                ],
                "next_cursor": next_cursor,
            }
        )
        audit_read(ctx, "read.decisions", count=len(body.items))
        return body.model_dump(mode="json")

    @router.post("/decisions/{card_id}/feedback")
    def feedback(card_id: UUID, request: FeedbackRequest, workspace_id: Workspace) -> Any:
        verdict = _text(request.verdict)
        action = _text(request.action) if request.action is not None else None
        try:
            with ctx.caller() as conn:
                if not card_visible(conn, workspace_id, card_id):
                    raise ApiError(404, "not_found", "Not found.")
                row = conn.execute(
                    "select public.record_decision_feedback(%s, %s, %s, %s) as id",
                    (card_id, verdict, action, _text(request.note)),
                ).fetchone()
        except psycopg.errors.InsufficientPrivilege:
            raise ApiError(404, "not_found", "Not found.") from None
        if row is None:
            raise ApiError(500, "internal_error", "The request could not be completed.")
        return MutationResponse.model_validate({"id": str(row["id"])}).model_dump(mode="json")

    @router.post("/clients/{client_id}/outcomes")
    def record_outcome(client_id: UUID, request: OutcomeRequest, workspace_id: Workspace) -> Any:
        _check_outcome(request)
        try:
            with ctx.caller() as conn:
                if not client_visible(conn, workspace_id, client_id):
                    raise ApiError(404, "not_found", "Not found.")
                row = conn.execute(
                    "select public.record_client_outcome(%s, %s, %s, %s, %s, %s, %s, %s) as id",
                    (
                        client_id,
                        _text(request.event),
                        _text(request.occurred_at),
                        _text(request.notice_at),
                        _text(request.first_known_at),
                        _text(request.amount_change),
                        _text(request.currency),
                        _text(request.note),
                    ),
                ).fetchone()
        except psycopg.errors.InsufficientPrivilege:
            raise ApiError(404, "not_found", "Not found.") from None
        if row is None:
            raise ApiError(500, "internal_error", "The request could not be completed.")
        return MutationResponse.model_validate({"id": str(row["id"])}).model_dump(mode="json")

    @router.delete("/outcomes/{outcome_id}", status_code=204)
    def delete_outcome(outcome_id: UUID, workspace_id: Workspace) -> Response:
        try:
            with ctx.caller() as conn:
                if not outcome_visible(conn, workspace_id, outcome_id):
                    raise ApiError(404, "not_found", "Not found.")
                conn.execute("select public.delete_client_outcome(%s)", (outcome_id,))
        except psycopg.errors.InsufficientPrivilege:
            raise ApiError(404, "not_found", "Not found.") from None
        return Response(status_code=204)

    return router
