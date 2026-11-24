"""Offline backfill uses the API connector's mapping and minimized encrypted upsert."""

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from firra.connectors.slack.export import ExportData
from firra.connectors.slack.ingest import mapped_channels, store_message
from firra.db.local_slack import SlackRepo
from firra.db.types import WorkspaceId
from firra.tasks.context import TaskContext
from firra.tasks.handlers.process import request_processing
from firra_contracts.generated.local.slack_import_response import SlackImportResponse


def import_messages(
    data: ExportData, *, tasks: TaskContext, workspace_id: WorkspaceId, connection_id: UUID
) -> SlackImportResponse:
    repo = SlackRepo(tasks.pool)
    channels: list[dict[str, Any]] = []
    for raw in data.channels.values():
        channel = dict(raw)
        if channel["is_im"] and not channel.get("user"):
            members = channel.get("members", [])
            external = (
                [
                    uid
                    for uid in members
                    if uid in data.users
                    and (data.users[uid].get("team_id") or data.users[uid].get("team"))
                    != data.team_id
                ]
                if isinstance(members, list)
                else []
            )
            # An export gives both DM participants; only a unique external peer can be mapped.
            if len(external) == 1:
                channel["user"] = external[0]
        channels.append(channel)
    selected = {
        channel["id"]: channel
        for channel in mapped_channels(
            repo,
            workspace_id=workspace_id,
            connection_id=connection_id,
            channels=channels,
            users=data.users,
            team_id=data.team_id,
            directory=tasks.directory(workspace_id),
        )
    }
    names = {
        uid: str((user.get("profile") or {}).get("display_name") or user.get("real_name") or "user")
        for uid, user in data.users.items()
    }
    imported = dropped = 0
    for raw_channel, message in data.messages():
        mapped_channel = selected.get(raw_channel["id"]) if raw_channel else None
        if mapped_channel is None or not isinstance(message, dict):
            dropped += 1
            continue
        try:
            stored = store_message(
                repo,
                workspace_id=workspace_id,
                connection_id=connection_id,
                team_id=data.team_id,
                users=data.users,
                names=names,
                message=message,
                channel=mapped_channel,
            )
        except (ValidationError, ValueError, OverflowError, TypeError):
            stored = None
        if stored is None:
            dropped += 1
        elif stored[1]:
            imported += 1
    request_processing(tasks, workspace_id, "ingest")
    return SlackImportResponse(
        channels=len(selected), messages_imported=imported, messages_dropped=dropped
    )
