"""Aggregate-only JSON and Markdown, without client or card identifiers."""

from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

from firra.learning.metrics import FamilyPrecision, LeadSummary, UsefulnessGroup, lead_time


def report_json(
    workspace_id: UUID,
    as_of: date,
    useful: Sequence[UsefulnessGroup],
    precise: Sequence[FamilyPrecision],
    lead: LeadSummary | None = None,
) -> dict[str, Any]:
    return {
        "workspace_id": str(workspace_id),
        "as_of": as_of.isoformat(),
        "usefulness": [g.as_json() for g in useful],
        "precision": [g.as_json() for g in precise],
        "lead_time": (lead if lead is not None else lead_time([], [])).as_json(),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Learning metrics — {report['workspace_id']}", f"As of {report['as_of']}"]
    lead = report["lead_time"]
    lead_rows = [{"family": "all", **{k: v for k, v in lead.items() if k != "families"}}]
    lead_rows += [
        {
            "family": row["family"],
            "detected": row["detected"],
            "missed": None,
            "recall": None,
            "median_lead_days": row["median_lead_days"],
            "share_ge_30d": row["share_ge_30d"],
        }
        for row in lead["families"]
    ]
    for title, rows in (
        ("Usefulness", report["usefulness"]),
        ("Precision", report["precision"]),
        ("Lead time", lead_rows),
    ):
        lines += ["", f"## {title}", ""]
        if not rows:
            lines.append("No observations.")
            continue
        lines += [
            "| " + " | ".join(rows[0]) + " |",
            "| " + " | ".join("---" for _ in rows[0]) + " |",
        ]
        for row in rows:
            values = [
                "null" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)
                for v in row.values()
            ]
            lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"
