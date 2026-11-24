"""Mapping suggestions use channel metadata and members; never message content."""

from collections.abc import Mapping, Sequence
from typing import Any

from firra.connectors.slack.client import SlackClient
from firra.policy.minimize import email_domain


def suggestions(
    client: SlackClient,
    channels: Sequence[Mapping[str, Any]],
    clients: Sequence[Mapping[str, Any]],
    users: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    ranked = []
    for channel in channels:
        label = str(channel.get("name") or channel["id"])
        lower = label.casefold()
        by_name = [
            c
            for c in clients
            if c["policy"] != "excluded"
            and (
                (len(str(c["name"])) >= 3 and str(c["name"]).casefold() in lower)
                or any(str(domain).split(".")[0].casefold() in lower for domain in c["domains"])
            )
        ]
        by_members = []
        member_ids = (
            client.members(str(channel["id"]))
            if not channel.get("is_im")
            else [channel.get("user")]
        )
        domains = {
            email_domain(str((users.get(str(member), {}).get("profile") or {}).get("email", "")))
            for member in member_ids
        }
        for candidate in clients:
            if candidate["policy"] != "excluded" and any(
                domain in domains for domain in candidate["domains"]
            ):
                by_members.append(candidate)
        matches = by_name or by_members
        suggested = matches[0]["id"] if len(matches) == 1 else None
        reason = (
            "channel_name"
            if by_name
            else "member_domain"
            if by_members
            else "slack_connect"
            if channel.get("is_ext_shared")
            else "unmatched"
        )
        kind = (
            "direct"
            if channel.get("is_im")
            else "group_direct"
            if channel.get("is_mpim")
            else "private"
            if channel.get("is_private")
            else "public"
        )
        score = (int(bool(channel.get("is_ext_shared"))), int(bool(by_name)), int(bool(by_members)))
        ranked.append(
            (
                score,
                {
                    "channel_id": channel["id"],
                    "label": label,
                    "kind": kind,
                    "suggested_client_id": str(suggested) if suggested else None,
                    "reason": reason,
                },
            )
        )
    ranked.sort(key=lambda entry: (tuple(-value for value in entry[0]), entry[1]["label"]))
    return [value for _, value in ranked]
