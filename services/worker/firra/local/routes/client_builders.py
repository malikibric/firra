"""Pure ports of attention.ts, changes.ts and clients.ts; no database access."""

from datetime import datetime, timedelta
from typing import Any

from firra.local.paging import instant
from firra_contracts.generated_v1.client_detail_response import Client, Inputs
from firra_contracts.generated_v1.clients_response import Item as ClientListItem

RISK = {"stable": 0, "watch": 1, "elevated": 2, "high": 3}
SEVERITY = {"low": 0, "medium": 1, "high": 2}
KINDS = (
    "risk_state_changed",
    "expansion_state_changed",
    "signal_opened",
    "signal_escalated",
    "signal_resolved",
)
BASICS = ("id", "name", "status", "policy", "mrr", "currency", "renewal_date", "notice_period_days")
SUMMARY = ("risk_state", "state_since", "expansion_state", "play", "as_of")


def pick(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: row[key] for key in keys if key in row}


def _drivers(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    for row in value:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("type"), str)
            or row.get("severity") not in SEVERITY
            or row.get("basis") not in ("evidence", "metadata")
            or (row.get("family") is not None and not isinstance(row["family"], str))
            or (row.get("occurred_at") is not None and not isinstance(row["occurred_at"], str))
            or ("primary" in row and type(row["primary"]) is not bool)
            or (row.get("inputs") is not None and not isinstance(row["inputs"], dict))
            or (
                "evidence_ids" in row
                and (
                    not isinstance(row["evidence_ids"], list)
                    or not all(isinstance(e, str) for e in row["evidence_ids"])
                )
            )
        ):
            return []
    return value


def build_attention(
    assessments: list[dict[str, Any]], clients: list[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    by_id = {client["id"]: client for client in clients}
    latest: dict[str, dict[str, Any]] = {}
    for row in assessments:
        if latest.get(row["client_id"], {}).get("as_of", "") < row["as_of"]:
            latest[row["client_id"]] = row
    since = (now - timedelta(days=14)).date().isoformat()
    items = []
    for row in latest.values():
        client = by_id.get(row["client_id"])
        if (
            not client
            or client["status"] != "active"
            or client["policy"] not in ("full", "metadata_only")
        ):
            continue
        changed = (
            row["previous_risk_state"] is not None
            and row["previous_risk_state"] != row["risk_state"]
            and row["state_since"] >= since
        )
        if RISK[row["risk_state"]] < RISK["elevated"] and not changed:
            continue
        drivers = _drivers(row["drivers"])
        days = next(
            (
                d.get("inputs", {}).get("days_to_notice")
                for d in drivers
                if d["type"] == "renewal_window"
                and isinstance(d.get("inputs"), dict)
                and type(d["inputs"].get("days_to_notice")) is int
            ),
            None,
        )
        # Stable secondary sorting preserves the TypeScript comparator's descending date.
        ranked = sorted(drivers, key=lambda d: d.get("occurred_at") or "", reverse=True)
        ranked.sort(
            key=lambda d: (
                d.get("family") == "expansion",
                not d.get("primary", False),
                -SEVERITY[d["severity"]],
            )
        )
        items.append(
            {
                "client_id": client["id"],
                "name": client["name"],
                "status": client["status"],
                "policy": client["policy"],
                **pick(row, SUMMARY),
                **pick(row, ("previous_risk_state", "strength")),
                "days_to_notice": days,
                "drivers": [
                    {
                        "type": d["type"],
                        "family": d.get("family"),
                        "severity": d["severity"],
                        "basis": d["basis"],
                        "occurred_at": d["occurred_at"][:10] if d.get("occurred_at") else None,
                        "primary": d.get("primary", False),
                        "evidence_count": len(d.get("evidence_ids", [])),
                    }
                    for d in ranked[:2]
                ],
            }
        )
    items.sort(key=lambda x: x["name"].casefold())
    items.sort(key=lambda x: x["state_since"], reverse=True)
    items.sort(
        key=lambda x: (
            -RISK[x["risk_state"]],
            x["days_to_notice"] if x["days_to_notice"] is not None else float("inf"),
        )
    )
    return items[:150]


def build_changes(
    assessments: list[dict[str, Any]],
    events: list[dict[str, Any]],
    clients: list[dict[str, Any]],
    since: str,
    now: datetime,
) -> list[dict[str, Any]]:
    visible = {
        c["id"]: c
        for c in clients
        if c["status"] == "active" and c["policy"] in ("full", "metadata_only")
    }
    items: list[dict[str, Any]] = []

    def base(client: dict[str, Any], day: str, kind: str) -> dict[str, Any]:
        return {
            "client_id": client["id"],
            "client_name": client["name"],
            "as_of": day,
            "kind": kind,
            "from": None,
            "to": None,
            "signal_type": None,
            "family": None,
            "severity": None,
            "evidence_count": 0,
        }

    previous_expansion: dict[str, str] = {}
    for row in sorted(assessments, key=lambda r: r["as_of"]):
        client = visible.get(row["client_id"])
        if client is None or row["as_of"] > now.date().isoformat():
            continue
        previous = previous_expansion.get(row["client_id"])
        previous_expansion[row["client_id"]] = row["expansion_state"]
        if row["as_of"] < since:
            continue
        if (
            row["previous_risk_state"] is not None
            and row["previous_risk_state"] != row["risk_state"]
        ):
            items.append(
                {
                    **base(client, row["as_of"], "risk_state_changed"),
                    "from": row["previous_risk_state"],
                    "to": row["risk_state"],
                }
            )
        if previous is not None and previous != row["expansion_state"]:
            items.append(
                {
                    **base(client, row["as_of"], "expansion_state_changed"),
                    "from": previous,
                    "to": row["expansion_state"],
                }
            )
    previous_severity: dict[str, str] = {}
    for row in sorted(
        events, key=lambda r: (instant(r["as_of"]), instant(r["created_at"]), r["id"])
    ):
        client = visible.get(row["client_id"])
        if client is None or instant(row["as_of"]) > instant(now):
            continue
        previous = previous_severity.get(row["signal_id"])
        previous_severity[row["signal_id"]] = row["severity"]
        day = instant(row["as_of"])[:10]
        if day < since:
            continue
        kind = (
            "signal_opened"
            if row["change"] in ("opened", "reopened")
            else "signal_resolved"
            if row["change"] == "resolved"
            else "signal_escalated"
            if previous is not None and SEVERITY[row["severity"]] > SEVERITY[previous]
            else None
        )
        if kind is None:
            continue
        signal = row["signal"]
        items.append(
            {
                **base(client, day, kind),
                "signal_type": signal["type"],
                "family": signal["family"],
                "severity": row["severity"],
                "evidence_count": 0
                if kind == "signal_resolved"
                or signal["basis"] == "metadata"
                or client["policy"] == "metadata_only"
                else len(signal["signal_evidence"]),
            }
        )
    items.sort(key=lambda r: (r["client_name"].casefold(), KINDS.index(r["kind"])))
    items.sort(key=lambda r: r["as_of"], reverse=True)
    return items[:300]


def build_state_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"as_of": row["as_of"], "from": row["previous_risk_state"], "to": row["risk_state"]}
        for row in sorted(rows, key=lambda r: r["as_of"], reverse=True)
        if row["previous_risk_state"] is not None
        and row["previous_risk_state"] != row["risk_state"]
    ][:20]


def build_client_list_item(
    client: dict[str, Any], assessment: dict[str, Any] | None
) -> dict[str, Any]:
    return ClientListItem.model_validate(
        {
            **pick(client, BASICS),
            "assessment": pick(assessment, SUMMARY) if assessment else None,
        }
    ).model_dump(mode="json", exclude_unset=True)


def _with_evidence(
    rows: list[dict[str, Any]], join: str, keys: tuple[str, ...]
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        links = sorted(row.get(join, []), key=lambda x: x["evidence_id"])
        links.sort(key=lambda x: instant(x["created_at"]), reverse=True)
        result.append(
            {**pick(row, keys), "evidence_ids": [link["evidence_id"] for link in links[:5]]}
        )
    return result


def build_client_detail(
    client: dict[str, Any],
    assessment: dict[str, Any] | None,
    history: list[dict[str, Any]],
    parts: dict[str, Any],
) -> dict[str, Any]:
    metadata = client["policy"] == "metadata_only"
    shaped_assessment = None
    if assessment:
        drivers = []
        for row in assessment["drivers"]:
            if metadata and row["basis"] != "metadata":
                continue
            driver = {
                **pick(row, ("type", "severity", "basis")),
                "family": row.get("family"),
                "occurred_at": row.get("occurred_at"),
                "primary": row.get("primary", False),
                "evidence_ids": [] if row["basis"] == "metadata" else row.get("evidence_ids", []),
            }
            if row["basis"] == "metadata":
                inputs = row.get("inputs") or {}
                driver["inputs"] = Inputs.model_validate(
                    {k: v for k, v in inputs.items() if k in Inputs.model_fields}
                ).model_dump(mode="json", exclude_unset=True)
            drivers.append(driver)
        shaped_assessment = {
            **pick(assessment, (*SUMMARY, "previous_risk_state", "strength")),
            "caveats": [pick(row, ("code", "detail")) for row in assessment["caveats"]],
            "drivers": drivers,
        }
    body = {
        **pick(client, BASICS),
        "assessment": shaped_assessment,
        "state_history": build_state_history(history),
        "stakeholders": [
            pick(
                row,
                ("display_name", "role_title", "is_decision_maker", "is_champion", "last_seen_at"),
            )
            for row in parts["stakeholders"]
        ],
        "unknowns": [
            pick(row, ("code", "detail"))
            for row in (parts.get("cardContent") or {}).get("unknowns", [])
        ],
        "commitments": []
        if metadata
        else _with_evidence(
            parts["commitments"],
            "commitment_evidence",
            ("owner_side", "text", "due_date", "status"),
        ),
        "goals": []
        if metadata
        else _with_evidence(
            parts["goals"], "client_goal_evidence", ("text", "metric", "stated_at")
        ),
        "needs": []
        if metadata
        else _with_evidence(
            parts["needs"],
            "latent_need_evidence",
            ("topic", "mention_count", "thread_count", "first_seen_at", "last_seen_at"),
        ),
        "outcomes": [
            pick(
                row,
                (
                    "event",
                    "occurred_at",
                    "notice_at",
                    "first_known_at",
                    "amount_change",
                    "currency",
                ),
            )
            for row in parts["outcomes"]
        ],
    }
    return Client.model_validate(body).model_dump(mode="json", by_alias=True, exclude_unset=True)
