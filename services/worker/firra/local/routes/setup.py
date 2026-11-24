"""Setup writes (clients, policies, services, connections, replay) over the existing RPCs.

Port of ``routes/setup.ts`` and ``setup-store.ts`` for the single local owner: invites, members and
contributors do not exist locally. Defaults of the zod contracts are applied before validation
because the generated models carry none. Reading mail again after a client becomes Full is wired
by the engine scheduler, not here.
"""

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

import psycopg.errors
from fastapi import APIRouter, Body, Depends, Response
from pydantic import ValidationError

from firra.db.local_setup import domains, services, target_visible
from firra.db.types import WorkspaceId
from firra.local.readctx import ReadContext
from firra.local.routes.common import audit_read, workspace_guard
from firra.local.server import ApiError
from firra.logging import get_logger
from firra_contracts.generated_v1.client_domains_request import ClientDomainsRequest
from firra_contracts.generated_v1.client_domains_response import ClientDomainsResponse
from firra_contracts.generated_v1.client_policy_request import ClientPolicyRequest
from firra_contracts.generated_v1.create_client_request import CreateClientRequest
from firra_contracts.generated_v1.request_replay_request import RequestReplayRequest
from firra_contracts.generated_v1.save_service_request import SaveServiceRequest
from firra_contracts.generated_v1.services_response import ServicesResponse
from firra_contracts.generated_v1.setup_id_response import SetupIdResponse
from firra_contracts.generated_v1.update_client_request import UpdateClientRequest

log = get_logger(__name__)
Body_ = Annotated[dict[str, Any], Body()]
_INVALID = ApiError(422, "invalid_request", "Invalid setup body.")
_NOT_FOUND = ApiError(404, "not_found", "Not found.")


def _parse[M: Any](model: type[M], body: dict[str, Any], defaults: dict[str, Any]) -> M:
    try:
        return model.model_validate({**defaults, **body})  # type: ignore[no-any-return]
    except ValidationError:
        raise _INVALID from None


def _policy_confirmed(policy: Any, body: dict[str, Any]) -> None:
    """Refinement of the zod contract: excluded deletes data and must be confirmed."""
    if (
        str(getattr(policy, "value", policy)) == "excluded"
        and body.get("confirm_exclusion") is not True
    ):
        raise _INVALID


def _id(row: Any) -> dict[str, Any]:
    if row is None:
        raise ApiError(500, "internal_error", "The request could not be completed.")
    return SetupIdResponse.model_validate({"id": str(row["id"])}).model_dump(mode="json")


def build_router(
    ctx: ReadContext, *, mail_scope_changed: Callable[[], None] | None = None
) -> APIRouter:
    router = APIRouter(prefix="/v1/workspaces/{workspace_id}")
    Workspace = Annotated[WorkspaceId, Depends(workspace_guard(ctx))]  # noqa: N806 - a type alias

    def run(
        workspace_id: WorkspaceId,
        table: str | None,
        target: UUID | None,
        sql: str,
        args: tuple[Any, ...],
    ) -> Any:
        """One RPC as the owner; a missing or foreign target and 42501 are both 404."""
        try:
            with ctx.caller() as conn:
                if (
                    table is not None
                    and target is not None
                    and not target_visible(conn, workspace_id, table, target)
                ):
                    raise _NOT_FOUND
                return conn.execute(sql, args).fetchone()
        except psycopg.errors.InsufficientPrivilege:
            raise _NOT_FOUND from None

    def reread_mail() -> None:
        """A client became readable: connections read their window again (best effort)."""
        if mail_scope_changed is None:
            return
        try:
            mail_scope_changed()
        except Exception as exc:  # the change itself succeeded; the owner can repeat it
            log.warning("setup.reread_failed", error_class=type(exc).__name__)

    @router.post("/clients")
    def create_client(workspace_id: Workspace, body: Body_) -> Any:
        req = _parse(
            CreateClientRequest, body, {"policy": "full", "domains": [], "confirm_exclusion": False}
        )
        _policy_confirmed(req.policy, {**{"confirm_exclusion": False}, **body})
        row = run(
            workspace_id,
            None,
            None,
            "select public.create_client(p_workspace_id => %s, p_name => %s, p_policy => %s,"
            " p_domains => %s) as id",
            (workspace_id, req.name, req.policy.value, [d.root for d in req.domains]),
        )
        if req.policy.value == "full" and req.domains:
            reread_mail()
        return _id(row)

    @router.patch("/clients/{client_id}", status_code=204)
    def update_client(workspace_id: Workspace, client_id: UUID, body: Body_) -> Response:
        req = _parse(UpdateClientRequest, body, {})
        run(
            workspace_id,
            "client",
            client_id,
            "select public.update_client(p_client_id => %s, p_name => %s, p_status => %s)",
            (client_id, req.name, req.status.value),
        )
        return Response(status_code=204)

    @router.put("/clients/{client_id}/domains", status_code=204)
    def set_domains(workspace_id: Workspace, client_id: UUID, body: Body_) -> Response:
        req = _parse(ClientDomainsRequest, body, {})
        run(
            workspace_id,
            "client",
            client_id,
            "select public.set_client_domains(p_client_id => %s, p_domains => %s)",
            (client_id, [d.root for d in req.domains]),
        )
        if req.domains:
            reread_mail()
        return Response(status_code=204)

    @router.put("/clients/{client_id}/policy", status_code=204)
    def set_policy(workspace_id: Workspace, client_id: UUID, body: Body_) -> Response:
        req = _parse(ClientPolicyRequest, body, {"confirm_exclusion": False})
        _policy_confirmed(req.policy, {"confirm_exclusion": False, **body})
        run(
            workspace_id,
            "client",
            client_id,
            "select public.set_client_policy(p_client_id => %s, p_policy => %s)",
            (client_id, req.policy.value),
        )
        if req.policy.value == "full":
            reread_mail()
        return Response(status_code=204)

    def save_service(
        workspace_id: WorkspaceId, service_id: UUID | None, body: dict[str, Any]
    ) -> Any:
        req = _parse(SaveServiceRequest, body, {"description": "", "keywords": []})
        keywords = [k.root.strip() for k in req.keywords]
        if any(not k for k in keywords) or len(",".join(keywords)) > 1000:
            raise _INVALID
        unique = list(dict.fromkeys(k.lower() for k in keywords))
        row = run(
            workspace_id,
            "workspace_service" if service_id else None,
            service_id,
            "select public.save_workspace_service(p_workspace_id => %s, p_service_id => %s,"
            " p_name => %s, p_description => %s, p_keywords => %s) as id",
            (workspace_id, service_id, req.name.strip(), req.description, unique),
        )
        return _id(row)

    @router.post("/services")
    def create_service(workspace_id: Workspace, body: Body_) -> Any:
        return save_service(workspace_id, None, body)

    @router.put("/services/{service_id}")
    def update_service(workspace_id: Workspace, service_id: UUID, body: Body_) -> Any:
        return save_service(workspace_id, service_id, body)

    @router.delete("/services/{service_id}", status_code=204)
    def delete_service(workspace_id: Workspace, service_id: UUID) -> Response:
        run(
            workspace_id,
            "workspace_service",
            service_id,
            "select public.delete_workspace_service(%s)",
            (service_id,),
        )
        return Response(status_code=204)

    @router.delete("/connections/{connection_id}", status_code=204)
    def disconnect(workspace_id: Workspace, connection_id: UUID) -> Response:
        run(
            workspace_id,
            "connection",
            connection_id,
            "select public.disconnect_connection(%s)",
            (connection_id,),
        )
        return Response(status_code=204)

    @router.post("/replay/runs")
    def request_replay(workspace_id: Workspace, body: Body_) -> Any:
        req = _parse(RequestReplayRequest, body, {"step_days": 7})
        days = (req.window_end - req.window_start).days
        if days < 0 or days > 400:
            raise _INVALID
        row = run(
            workspace_id,
            None,
            None,
            "select public.request_replay_run(p_workspace_id => %s, p_window_start => %s,"
            " p_window_end => %s, p_step_days => %s) as id",
            (workspace_id, req.window_start, req.window_end, req.step_days),
        )
        return _id(row)

    @router.get("/services")
    def list_services(workspace_id: Workspace) -> Any:
        with ctx.caller() as conn:
            items = services(conn, workspace_id)
        body = ServicesResponse.model_validate({"items": items})
        audit_read(ctx, "read.setup_services", count=len(items))
        return body.model_dump(mode="json")

    @router.get("/clients/{client_id}/domains")
    def list_domains(workspace_id: Workspace, client_id: UUID) -> Any:
        with ctx.caller() as conn:
            if not target_visible(conn, workspace_id, "client", client_id):
                raise _NOT_FOUND
            found = domains(conn, workspace_id, client_id)
        body = ClientDomainsResponse.model_validate({"domains": found})
        audit_read(ctx, "read.setup_domains", count=len(found))
        return body.model_dump(mode="json")

    return router
