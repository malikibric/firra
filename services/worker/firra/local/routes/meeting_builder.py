"""Pure port of meetings.ts, including section-level catch/default behaviour."""

import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from firra_contracts.generated_v1.meetings_response import (
    OpenCommitment,
    Question,
    StakeholderChange,
    Unknown,
    UnresolvedComplaint,
)

UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return at.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except ValueError:
        return None


def _day(value: Any) -> str | None:
    return value if isinstance(value, str) and DAY_PATTERN.fullmatch(value) else None


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return []
    return [v for v in value if UUID_PATTERN.fullmatch(v)][:5]


def _section(value: Any, model: type[BaseModel]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows = []
    try:
        for row in value:
            if not isinstance(row, dict):
                return []
            shaped = {key: row[key] for key in model.model_fields if key in row}
            if "text" in model.model_fields and (
                not isinstance(row.get("text"), str) or not row["text"]
            ):
                return []
            if "evidence_ids" in model.model_fields:
                shaped["evidence_ids"] = _ids(row.get("evidence_ids"))
            if model is OpenCommitment:
                if row.get("due") is not None and not isinstance(row["due"], str):
                    return []
                shaped["due"] = _day(row.get("due"))
                shaped["late"] = row.get("late") if type(row.get("late")) is bool else False
            if model is UnresolvedComplaint:
                subtype = row.get("subtype")
                shaped["subtype"] = (
                    subtype if isinstance(subtype, str) and len(subtype) <= 40 else "other"
                )
            if model is Question and isinstance(row.get("basis"), dict):
                shaped["basis"] = {
                    key: row["basis"][key] for key in ("kind", "ref") if key in row["basis"]
                }
            parsed = model.model_validate_json(json.dumps(shaped), strict=True)
            rows.append(parsed.model_dump(mode="json", exclude_unset=True))
    except ValidationError:
        return []
    return rows


def build_meetings(
    briefs: list[dict[str, Any]],
    events: list[dict[str, Any]],
    clients: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    event_by_id = {e["id"]: e for e in events}
    client_by_id = {c["id"]: c for c in clients}
    items = []
    for brief in briefs:
        starts = _iso(brief["starts_at"])
        event = event_by_id.get(brief["calendar_event_id"])
        client = client_by_id.get(brief["client_id"])
        if (
            starts is None
            or event is None
            or client is None
            or event["status"] != "scheduled"
            or _iso(event["starts_at"]) != starts
            or client["status"] != "active"
            or client["policy"] not in ("full", "metadata_only")
            or datetime.fromisoformat(starts.replace("Z", "+00:00")) <= now
        ):
            continue
        content = brief["content"]
        if not isinstance(content, dict):
            continue
        full = client["policy"] == "full"
        last = content.get("since_last_meeting")
        if (
            not isinstance(last, dict)
            or type(last.get("records")) is not int
            or last["records"] < 0
            or (last.get("last_held_at") is not None and not isinstance(last["last_held_at"], str))
        ):
            last = {"records": 0, "last_held_at": None}
        renewal = content.get("renewal")
        if (
            not isinstance(renewal, dict)
            or _day(renewal.get("date")) is None
            or (
                renewal.get("days_to_notice") is not None
                and type(renewal["days_to_notice"]) is not int
            )
        ):
            renewal = None
        else:
            renewal = {"date": renewal["date"], "days_to_notice": renewal.get("days_to_notice")}
        questions = _section(content.get("questions"), Question)
        items.append(
            {
                **{key: brief[key] for key in ("id", "calendar_event_id", "client_id")},
                "client_name": client["name"],
                "policy": client["policy"],
                "starts_at": starts,
                "basis": brief["basis"] if full else "metadata",
                "since_last_meeting": {
                    "records": last["records"],
                    "last_held_at": _iso(last.get("last_held_at")),
                },
                "open_commitments": _section(content.get("open_commitments"), OpenCommitment)[:5]
                if full
                else [],
                "unresolved_complaints": _section(
                    content.get("unresolved_complaints"), UnresolvedComplaint
                )[:5]
                if full
                else [],
                "stakeholder_changes": _section(
                    content.get("stakeholder_changes"), StakeholderChange
                )[:5]
                if full
                else [],
                "renewal": renewal,
                "unknowns": _section(content.get("unknowns"), Unknown)[:10],
                "questions": [
                    q for q in questions if full or q["basis"]["kind"] in ("renewal", "unknown")
                ][:3],
            }
        )
    return sorted(items, key=lambda row: (row["starts_at"], row["id"]))[:10]
