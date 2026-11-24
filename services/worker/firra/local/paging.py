"""Cursor paging of the `/v1` lists: a port of ``apps/api/src/lib/read-page.ts`` (same cursors).

A cursor is ``base64url("<occurred_at>|<id>")``. Rows sort newest first by ``(occurred_at, id)``;
a page holds ``limit`` rows and ``next_cursor`` is set when more follow. Cursors are validated
strictly (canonical encoding, ISO instant with at most microseconds, UUID or event-id shape) so a
crafted cursor never reaches SQL.
"""

import base64
import binascii
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

_EVENT_ID = re.compile(
    r"^(?:state|signal|outcome|commitment_(?:created|due|closed)):[0-9a-f-]{36}$"
)
_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(\d+))?(?:Z|[+-]\d{2}:\d{2})$")
_CURSOR_CHARS = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Cursor:
    occurred_at: str
    id: str


@dataclass(frozen=True)
class PageQuery:
    limit: int
    cursor: Cursor | None


def instant(value: str | datetime) -> str:
    """Canonical UTC instant with exactly six fractional digits (lexically sortable)."""
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    utc = parsed.astimezone(datetime.fromisoformat("2000-01-01T00:00:00+00:00").tzinfo)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond:06d}Z"


def _b64decode(cursor: str) -> str | None:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode()).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    reencoded = base64.urlsafe_b64encode(decoded.encode()).decode().rstrip("=")
    return decoded if reencoded == cursor else None


def parse_page(
    limit: str = "50", cursor: str | None = None, *, evidence: bool = False
) -> PageQuery | None:
    """``None`` for anything invalid (the route answers 422)."""
    if not re.fullmatch(r"[0-9]+", limit) or not 1 <= int(limit) <= 100:
        return None
    if cursor is None:
        return PageQuery(int(limit), None)
    if not _CURSOR_CHARS.fullmatch(cursor) or len(cursor) > 256:
        return None
    decoded = _b64decode(cursor)
    if decoded is None:
        return None
    parts = decoded.split("|")
    if len(parts) != 2:
        return None
    occurred_at, ident = parts
    match = _INSTANT.fullmatch(occurred_at)
    if match is None or len(match.group(1) or "") > 6:
        return None
    target = ident if evidence else ident.split(":", 1)[-1] if ":" in ident else ""
    try:
        UUID(target)
    except ValueError:
        return None
    if not evidence and not _EVENT_ID.fullmatch(ident):
        return None
    return PageQuery(int(limit), Cursor(instant(occurred_at), ident))


def page(
    rows: Sequence[Mapping[str, Any]], query: PageQuery
) -> tuple[list[dict[str, Any]], str | None]:
    """Newest first, after the cursor: ``(items, next_cursor)`` like ``page()`` in read-page.ts."""
    prepared = [{**row, "occurred_at": instant(row["occurred_at"])} for row in rows]
    cursor = query.cursor
    kept = [
        r
        for r in prepared
        if cursor is None
        or r["occurred_at"] < cursor.occurred_at
        or (r["occurred_at"] == cursor.occurred_at and str(r["id"]) < cursor.id)
    ]
    kept.sort(key=lambda r: (r["occurred_at"], str(r["id"])), reverse=True)
    items = kept[: query.limit]
    last = items[-1] if items else None
    next_cursor = (
        base64.urlsafe_b64encode(f"{last['occurred_at']}|{last['id']}".encode())
        .decode()
        .rstrip("=")
        if len(kept) > query.limit and last
        else None
    )
    return items, next_cursor
