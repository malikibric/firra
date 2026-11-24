"""Guards and the audited-read helper shared by the local ``/v1`` routes."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from firra.db.local_reads import write_read_audit
from firra.db.types import WorkspaceId
from firra.local.readctx import ReadContext
from firra.local.server import ApiError


def workspace_guard(ctx: ReadContext) -> Callable[[UUID], WorkspaceId]:
    """Path dependency: the one local workspace is the only one there is (anything else: 403)."""

    def check(workspace_id: UUID) -> WorkspaceId:
        if workspace_id != ctx.identity.workspace_id:
            raise ApiError(403, "forbidden", "Owner or admin access is required.")
        return WorkspaceId(workspace_id)

    return check


WorkspacePath = Annotated[WorkspaceId, Depends]  # documents intent; routes use Depends(guard)


def audit_read(
    ctx: ReadContext,
    action: str,
    *,
    count: int,
    target_type: str | None = None,
    target_id: UUID | None = None,
) -> None:
    """Writes the audit row **before** data is returned; a failure raises, nothing is returned."""
    with ctx.pool.connection() as conn:
        write_read_audit(
            conn,
            workspace_id=WorkspaceId(ctx.identity.workspace_id),
            actor_id=ctx.identity.user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            count=count,
        )


def request_id(request: Request) -> str:
    return str(request.state.request_id)
