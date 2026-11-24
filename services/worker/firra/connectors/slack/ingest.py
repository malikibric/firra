"""One minimized ingestion path for API polling and offline exports."""

import hashlib
from collections.abc import Callable
from typing import Any
from uuid import UUID

from firra.connectors.slack.normalize import normalize
from firra.connectors.slack.resolve import dm_client, side_and_direction
from firra.db.local_slack import SlackRepo
from firra.db.records import RecordInput
from firra.db.types import WorkspaceId
from firra.policy.minimize import Directory


def store_message(
    repo: SlackRepo,
    *,
    workspace_id: WorkspaceId,
    connection_id: UUID,
    team_id: str,
    users: dict[str, dict[str, Any]],
    names: dict[str, str],
    message: dict[str, Any],
    channel: dict[str, Any],
    permalink: Callable[[str, str], str | None] | None = None,
) -> tuple[UUID, bool] | None:
    user_id = str(message.get("user", ""))
    user = users.get(user_id)
    if user is None:
        return None
    resolved = side_and_direction(user, channel, team_id)
    if resolved is None:
        return None
    mapping = channel["mapping"]
    record = normalize(message, channel, user, team_id=team_id, users=names)
    if record is None:
        return None
    side, direction = resolved
    full = mapping["policy"] == "full"
    metadata = {"channel_id": channel["id"], "edited": record.edited}
    if full:
        metadata.update(
            channel=channel.get("name") or channel["id"],
            author_name=record.author.name,
            author_slack_id=record.author.slack_id,
        )
    deep_link = permalink(channel["id"], str(message["ts"])) if full and permalink else None
    row = RecordInput(
        connection_id=connection_id,
        provider="slack",
        external_id=record.external_id,
        kind="chat",
        client_id=mapping["client_id"],
        policy=mapping["policy"],
        thread_key=record.thread_key,
        occurred_at=record.occurred_at,
        direction=direction,
        word_count=len(record.text.split()),
        metadata=metadata,
        body_clean=record.text if full else None,
        raw_object_uri=None,
        deep_link=deep_link,
        content_hash=hashlib.sha256(record.text.encode()).digest() if full else None,
    )
    stored = repo.store(
        workspace_id=workspace_id,
        record=row,
        slack_id=record.author.slack_id,
        name=record.author.name,
        email=record.author.email,
        side=side,
        require_scope=not mapping.get("dm", False),
    )
    return stored


def mapped_channels(
    repo: SlackRepo,
    *,
    workspace_id: WorkspaceId,
    connection_id: UUID,
    channels: list[dict[str, Any]],
    users: dict[str, dict[str, Any]],
    team_id: str,
    directory: Directory,
) -> list[dict[str, Any]]:
    mappings = {
        row["external_id"]: row
        for row in repo.mappings(workspace_id=workspace_id, connection_id=connection_id)
    }
    selected = []
    for channel in channels:
        mapping = mappings.get(channel["id"])
        if mapping is None and channel.get("is_im"):
            user_id = str(channel.get("user", ""))
            user = users.get(user_id)
            if user is not None:
                ref = dm_client(
                    repo,
                    workspace_id=workspace_id,
                    user=user,
                    team_id=team_id,
                    directory=directory,
                )
                if ref is not None and ref["policy"] != "excluded":
                    mapping = {"client_id": ref["id"], "policy": ref["policy"], "dm": True}
        if mapping is not None:
            selected.append({**channel, "mapping": mapping})
    return sorted(selected, key=lambda channel: channel["id"])
