"""Slack identity and direction are resolved only after a channel/DM is mapped."""

from collections.abc import Mapping
from typing import Any, Literal

from firra.db.local_slack import SlackRepo
from firra.db.types import WorkspaceId
from firra.policy.minimize import Directory, client_for


def dm_client(
    repo: SlackRepo,
    *,
    workspace_id: WorkspaceId,
    user: Mapping[str, Any],
    team_id: str,
    directory: Directory,
) -> dict[str, Any] | None:
    user_team = user.get("team_id") or user.get("team")
    if user_team == team_id or user.get("is_bot") or user.get("deleted"):
        return None
    mapped = repo.mapped_person(workspace_id=workspace_id, slack_id=str(user["id"]))
    if mapped:
        return mapped
    email = (user.get("profile") or {}).get("email")
    client = client_for(email, directory) if isinstance(email, str) else None
    return {"id": client.id, "name": client.name, "policy": client.policy} if client else None


def side_and_direction(
    user: Mapping[str, Any], channel: Mapping[str, Any], team_id: str
) -> tuple[Literal["client", "firm"], Literal["in", "out", "internal"]] | None:
    author_team = user.get("team_id") or user.get("team")
    if not author_team:
        return None
    external = author_team != team_id
    side: Literal["client", "firm"] = "client" if external else "firm"
    if channel.get("is_im") or channel.get("is_mpim") or channel.get("is_ext_shared"):
        return side, "in" if external else "out"
    return side, "internal"
