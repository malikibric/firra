"""Slack message text cleaning: mrkdwn to the plain text the author wrote.

Docs checked (2026-10-05): https://docs.slack.dev/messaging/formatting-message-text
("Formatting message text": mentions ``<@U...>``, channel links ``<#C...>``,
``<!here|...>/<!channel>/<!everyone>``, ``<!subteam^ID>``, manual links
``<url|label>``, ``<mailto:...>``, HTML entities ``&amp;/&lt;/&gt;``, colon
emoji in retrieved messages, inline/fenced code, ``*bold*``/``_italic_``/
``~strike~``, ``>`` quotes, ``<!date^...|fallback>``) and
https://docs.slack.dev/reference/events/message ("message event": the subtype
table used by ``is_system_message``, ``edited`` payload shape).
Confirmed: all of the above syntax forms exist in retrieved history payloads.
Could not confirm: exact subtype strings for huddle/call notifications (no
huddle/call subtype in the event reference; polling sees them, if at all, as
unlisted subtypes, which default to system); whether ``text`` can be absent
when ``blocks`` are present (history payloads always carry ``text`` for user
messages, so ``text`` stays the only source here; ``blocks`` belong to L2S-04).
``text`` vs ``blocks`` precedence therefore does not make ``text`` unreliable.

Steps, all pure: code spans are lifted out first (restored verbatim without
backticks at the end); ``<...>`` constructs expand to readable text; entities
unescape; emoji codes drop (``:+1:``-style kept); inline markers go; whitespace
collapses. ``clean_slack_text`` is idempotent on realistic messages; ``>``
quoted lines stay (the author wrote them, unlike mail quoting).
"""

import re
from collections.abc import Mapping
from typing import Any

_FENCED_CODE = re.compile(r"```(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_ANGLE = re.compile(r"<([^<>\n]+)>")
_EMOJI = re.compile(r":([A-Za-z0-9_+\-]+):")
_KEPT_EMOJI = re.compile(r"[+-][\w+-]*")
_BOLD = re.compile(r"\*([^*\n]+)\*")
_ITALIC = re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)")
_STRIKE = re.compile(r"~([^~\n]+)~")
_BLANK_RUN = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")
_LINE_TRAIL = re.compile(r"[ \t]+\n")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_PLACEHOLDER = "\ue000"

_SYSTEM_SUBTYPES = frozenset(
    {
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "channel_archive",
        "channel_unarchive",
        "channel_convert_to_private",
        "channel_convert_to_public",
        "channel_posting_permissions",
        "group_join",
        "group_leave",
        "group_topic",
        "group_purpose",
        "group_name",
        "group_archive",
        "group_unarchive",
        "bot_message",
        "reminder_add",
        "pinned_item",
        "unpinned_item",
        "file_mention",
    }
)
# Human prose we always keep, even with a subtype attached.
_KEEP_SUBTYPES = frozenset({"thread_broadcast", "me_message"})


def _mention(body: str, users: Mapping[str, str]) -> str:
    if "|" in body:
        user_id, _, label = body[1:].partition("|")
        return "@" + users.get(user_id, label or "user")
    user_id = body[1:]
    return "@" + users.get(user_id, "user")


def _channel(body: str, channels: Mapping[str, str] | None) -> str:
    if "|" in body:
        channel_id, _, label = body[1:].partition("|")
        if label:
            return "#" + label
        return "#" + (channels or {}).get(channel_id, "channel")
    channel_id = body[1:]
    return "#" + (channels or {}).get(channel_id, "channel")


def _special(body: str) -> str | None:
    if body in ("!here", "!channel", "!everyone"):
        return "@" + body[1:]
    if body.startswith("!subteam"):
        _, _, label = body.partition("|")
        label = label[1:] if label.startswith("@") else label
        return "@" + (label or "group")
    if body.startswith("!date"):
        _, _, label = body.partition("|")
        return label
    return None


def _link(body: str, original: str) -> str:
    if body.startswith("mailto:"):
        address, _, _ = body[len("mailto:") :].partition("|")
        return address
    if "|" in body:
        url, _, label = body.partition("|")
        if url.startswith(("http://", "https://")):
            if label and label != url:
                return f"{label} ({url})"
            return url
    elif body.startswith(("http://", "https://")):
        return body
    return original


def _expand_angle(
    match: re.Match[str], users: Mapping[str, str], channels: Mapping[str, str] | None
) -> str:
    body = match.group(1)
    if body.startswith("@"):
        return _mention(body, users)
    if body.startswith("#"):
        return _channel(body, channels)
    if body.startswith("!"):
        special = _special(body)
        if special is not None:
            return special
        return match.group(0)
    return _link(body, match.group(0))


def clean_slack_text(
    text: str, users: Mapping[str, str], channels: Mapping[str, str] | None = None
) -> str:
    """Turn a Slack history ``text`` into the plain words the author wrote."""
    kept: list[str] = []

    def lift(pattern: re.Pattern[str], source: str) -> str:
        def hold(match: re.Match[str]) -> str:
            kept.append(match.group(1))
            return f"{_PLACEHOLDER}{len(kept) - 1}{_PLACEHOLDER}"

        return pattern.sub(hold, source)

    lifted = lift(_FENCED_CODE, lift(_INLINE_CODE, text))
    expanded = _ANGLE.sub(lambda m: _expand_angle(m, users, channels), lifted)
    unescaped = expanded.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

    def emoji(match: re.Match[str]) -> str:
        return f":{match.group(1)}:" if _KEPT_EMOJI.fullmatch(match.group(1)) else ""

    plain = _EMOJI.sub(emoji, unescaped)
    plain = _BOLD.sub(r"\1", plain)
    plain = _ITALIC.sub(r"\1", plain)
    plain = _STRIKE.sub(r"\1", plain)

    def drop(match: re.Match[str]) -> str:
        return kept[int(match.group(1))]

    restored = re.sub(f"{_PLACEHOLDER}(\\d+){_PLACEHOLDER}", drop, plain)
    collapsed = _BLANK_RUN.sub("\n\n", restored)
    collapsed = _LINE_TRAIL.sub("\n", collapsed)
    collapsed = _MULTI_SPACE.sub(" ", collapsed)
    return collapsed.strip()


def is_system_message(message: Mapping[str, Any]) -> bool:
    """True when the payload is not author-written prose.

    Any ``bot_id`` is a bot; any subtype outside the small keep-list is a
    join/leave/topic/event notice (huddle and call notifications arrive, if at
    all, as unlisted subtypes and therefore default to system). ``file_share``
    counts as prose only when it carries text.
    """
    if message.get("bot_id") is not None:
        return True
    subtype = message.get("subtype")
    if subtype is None:
        return False
    if subtype in _KEEP_SUBTYPES:
        return False
    if subtype == "file_share":
        return not str(message.get("text") or "").strip()
    return True
