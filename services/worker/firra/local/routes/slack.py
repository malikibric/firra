"""Slack setup after a memory-only token handoff, with owner-RLS visibility checks."""

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Response
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, ConfigDict, Field

from firra.connectors.slack.auth import SlackTokenRefusedError, revoke, validate_token
from firra.connectors.slack.client import SlackClient
from firra.connectors.slack.export import ExportError, read_export
from firra.connectors.slack.importer import import_messages
from firra.connectors.slack.local_tokens import LocalSlackTokens, token_slot
from firra.connectors.slack.suggest import suggestions
from firra.db import local_connections, local_slack
from firra.db.local_decisions import client_visible
from firra.db.types import WorkspaceId
from firra.local.readctx import ReadContext
from firra.local.secrets import SecretStore
from firra.local.server import ApiError
from firra.tasks.context import TaskContext
from firra_contracts import TaskEnvelope
from firra_contracts.generated.local.slack_import_response import SlackImportResponse


class SlackConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    account_label: str = Field(min_length=1, max_length=200)


class SlackImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)


class SlackChannelScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=64, pattern="^[A-Z0-9]+$")
    label: str | None = Field(default=None, max_length=200)
    client_id: UUID | None = None
    include: bool = Field(default=True, strict=True)
    meta: dict[str, Any] = Field(default_factory=dict)


class SlackScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channels: list[SlackChannelScope] = Field(max_length=500)


def build_router(
    ctx: ReadContext,
    *,
    secrets: SecretStore,
    tasks: TaskContext,
    send_secret_delete: Callable[[str], None],
) -> APIRouter:
    router = APIRouter()
    connection_prefix = "/local/connections/slack"
    workspace_id = WorkspaceId(ctx.identity.workspace_id)
    tokens = LocalSlackTokens(secrets)

    def find(connection_id: UUID) -> Any:
        with ctx.caller() as conn:
            row = local_connections.visible(conn, workspace_id, connection_id)
        if row is None or row["provider"] != "slack":
            raise ApiError(404, "not_found")
        connection = tasks.connections.get(workspace_id=workspace_id, connection_id=connection_id)
        if connection is None:
            raise ApiError(404, "not_found")
        return connection

    def enqueue(connection_id: UUID) -> None:
        key = f"slack-now:{connection_id}:{uuid4()}"
        payload = {"connection_id": str(connection_id)}
        with ctx.pool.connection() as conn:
            job_id = local_connections.enqueue_job(
                conn,
                workspace_id=workspace_id,
                connection_id=connection_id,
                job_type="sync.slack",
                key=key,
                payload=payload,
            )
        tasks.queue.enqueue(
            TaskEnvelope(
                workspace_id=workspace_id,
                job_id=job_id,
                idempotency_key=key,
                type="sync.slack",
                payload=payload,
            )
        )

    @router.post("/local/slack/import", response_model=SlackImportResponse)
    def import_export(body: SlackImportRequest) -> SlackImportResponse:
        try:
            with read_export(body.path) as data:
                # Team identity is engine-only cursor metadata; prove owner RLS visibility
                # of the selected connection before any mapping or record write.
                with ctx.pool.connection() as conn:
                    try:
                        connection_id = local_slack.open_export_connection(
                            conn,
                            workspace_id=workspace_id,
                            user_id=ctx.identity.user_id,
                            team_id=data.team_id,
                        )
                    except ValueError as exc:
                        raise ApiError(409, str(exc)) from None
                find(connection_id)
                repo = local_slack.SlackRepo(ctx.pool)
                with repo.syncing(workspace_id=workspace_id, connection_id=connection_id) as locked:
                    if not locked:
                        raise ApiError(409, "slack_sync_in_progress")
                    return import_messages(
                        data, tasks=tasks, workspace_id=workspace_id, connection_id=connection_id
                    )
        except ExportError as exc:
            raise ApiError(422, str(exc)) from None

    @router.post(connection_prefix)
    def connect(body: SlackConnectionRequest) -> dict[str, str]:
        token = secrets.get(token_slot(body.connection_id))
        if not token:
            raise ApiError(409, "missing_secret")
        try:
            identity = validate_token(tasks.http, token)
            details = SlackClient(tasks.http, token).auth_test()
        except SlackTokenRefusedError as exc:
            raise ApiError(422, exc.code) from None
        try:
            with ctx.pool.connection() as conn:
                connection_id = local_slack.connect(
                    conn,
                    workspace_id=workspace_id,
                    user_id=ctx.identity.user_id,
                    connection_id=body.connection_id,
                    account_label=body.account_label,
                    team_id=identity.team_id,
                    slack_user_id=identity.user_id,
                    workspace_url=details.get("url"),
                )
        except UniqueViolation:
            raise ApiError(409, "account_already_connected") from None
        if connection_id is None:
            raise ApiError(409, "connection_conflict")
        enqueue(connection_id)
        return {"connection_id": str(connection_id), "team_id": identity.team_id}

    @router.get(connection_prefix + "/{connection_id}/channels")
    def channels(connection_id: UUID) -> dict[str, Any]:
        connection = find(connection_id)
        if connection.status == "revoked":
            raise ApiError(409, "connection_unavailable")
        client = SlackClient(
            tasks.http,
            tokens.access_token(connection_id),
            workspace_url=connection.cursor.get("workspace_url"),
        )
        channel_list = list(client.channels())
        users = {str(user["id"]): user for user in client.users()}
        with ctx.caller() as conn:
            clients = local_slack.clients(conn, workspace_id=workspace_id)
        return {"items": suggestions(client, channel_list, clients, users)}

    @router.put(connection_prefix + "/{connection_id}/scope")
    def scope(connection_id: UUID, body: SlackScopeRequest) -> dict[str, str]:
        connection = find(connection_id)
        if connection.status == "revoked":
            raise ApiError(409, "connection_unavailable")
        with ctx.caller() as conn:
            for channel in body.channels:
                if channel.include and channel.client_id is None:
                    raise ApiError(422, "invalid_request")
                if channel.client_id and not client_visible(conn, workspace_id, channel.client_id):
                    raise ApiError(404, "not_found")
        with ctx.pool.connection() as conn, conn.transaction():
            for channel in body.channels:
                local_slack.set_scope(
                    conn,
                    workspace_id=workspace_id,
                    connection_id=connection_id,
                    channel_id=channel.external_id,
                    label=channel.label,
                    client_id=channel.client_id,
                    include=channel.include,
                    meta=channel.meta,
                )
        if connection.mode == "upload":
            return {"status": "saved"}
        enqueue(connection_id)
        return {"status": "queued"}

    @router.delete(connection_prefix + "/{connection_id}", status_code=204)
    def disconnect(connection_id: UUID) -> Response:
        find(connection_id)
        token = secrets.get(token_slot(connection_id))
        with ctx.pool.connection() as conn:
            local_slack.disconnect(conn, workspace_id=workspace_id, connection_id=connection_id)
        if token:
            revoke(tasks.http, token)
        name = token_slot(connection_id)
        secrets.delete(name)
        send_secret_delete(name)
        return Response(status_code=204)

    return router
