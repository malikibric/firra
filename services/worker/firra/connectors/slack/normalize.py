"""Slack payloads to canonical chat messages; bots and system notices are dropped."""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from firra.clean.slack import clean_slack_text, is_system_message
from firra_contracts import ChatMessage

_TS = re.compile(r"^[0-9]+\.[0-9]{6}$")


def normalize(
    message: Mapping[str, Any],
    channel: Mapping[str, Any],
    user: Mapping[str, Any],
    *,
    team_id: str,
    users: Mapping[str, str],
    permalink: str | None = None,
) -> ChatMessage | None:
    ts = str(message.get("ts", ""))
    if (
        message.get("type", "message") != "message"
        or is_system_message(message)
        or user.get("is_bot")
        or user.get("deleted")
        or not message.get("user")
        or not _TS.fullmatch(ts)
    ):
        return None
    text = clean_slack_text(str(message.get("text") or ""), users)
    if not text:
        return None
    profile = user.get("profile") or {}
    kind = (
        "direct"
        if channel.get("is_im")
        else "group_direct"
        if channel.get("is_mpim")
        else "private"
        if channel.get("is_private")
        else "public"
    )
    author_team = user.get("team_id") or user.get("team")
    return ChatMessage.model_validate(
        {
            "provider": "slack",
            "external_id": f"{channel['id']}:{ts}",
            "channel": {
                "id": channel["id"],
                "name": channel.get("name"),
                "kind": kind,
                "is_ext_shared": bool(channel.get("is_ext_shared")),
            },
            "thread_key": message.get("thread_ts"),
            "occurred_at": datetime.fromtimestamp(float(Decimal(ts)), UTC),
            "author": {
                "slack_id": message["user"],
                "name": profile.get("display_name")
                or profile.get("real_name")
                or user.get("real_name"),
                "email": profile.get("email"),
                "team_id": author_team,
                "is_external": author_team != team_id if author_team else None,
            },
            "text": text,
            "edited": isinstance(message.get("edited"), dict),
            "permalink": permalink,
        }
    )
