"""Explicitly confirmed mutations, with RPC and MCP audit in one transaction."""

from typing import Any
from uuid import UUID

from firra.db import local_mcp
from firra.db.types import WorkspaceId
from firra.local.readctx import ReadContext
from firra.local.routes.decisions import _check_outcome, _text
from firra_contracts.generated_v1.feedback_request import FeedbackRequest
from firra_contracts.generated_v1.mutation_response import MutationResponse
from firra_contracts.generated_v1.outcome_request import OutcomeRequest


def write(ctx: ReadContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    ws = WorkspaceId(ctx.identity.workspace_id)
    with ctx.caller() as conn:
        if name == "record_feedback":
            body = FeedbackRequest.model_validate({k: v for k, v in args.items() if k != "card_id"})
            result = local_mcp.feedback(
                conn,
                workspace_id=ws,
                card_id=UUID(str(args["card_id"])),
                verdict=_text(body.verdict),
                action=_text(body.action),
                note=_text(body.note),
            )
            target = None
        else:
            outcome_body = OutcomeRequest.model_validate(
                {k: v for k, v in args.items() if k != "client_id"}
            )
            _check_outcome(outcome_body)
            target = UUID(str(args["client_id"]))
            result = local_mcp.outcome(
                conn,
                workspace_id=ws,
                client_id=target,
                values=tuple(
                    _text(getattr(outcome_body, k))
                    for k in (
                        "event",
                        "occurred_at",
                        "notice_at",
                        "first_known_at",
                        "amount_change",
                        "currency",
                        "note",
                    )
                ),
            )
        local_mcp.audit_after_mutation(
            conn, workspace_id=ws, actor_id=ctx.identity.user_id, name=name, target_id=target
        )
    return MutationResponse(id=result).model_dump(mode="json")
