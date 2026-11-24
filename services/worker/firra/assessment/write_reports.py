"""Builds the Churn Replay report files of one finished run (local CLI helper)."""

import re
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.assessment.narrative import Narrative, StepRow, build_narrative, narrative_from_json
from firra.assessment.report import QuoteView, render_client, render_index
from firra.db.replay import ReplayRepo, ReportRepo
from firra.db.types import WorkspaceId


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "client"


def _rows(raw: list[dict[str, Any]]) -> list[StepRow]:
    return [
        StepRow(
            as_of=r["as_of"],
            risk=r["risk"],
            expansion=r["expansion"],
            play=r["play"],
            strength=r["strength"],
            drivers=tuple(r["drivers"]),
            caveats=tuple(c["code"] for c in r["caveats"]),
        )
        for r in raw
    ]


def write_reports(
    pool: ConnectionPool[Connection[DictRow]],
    *,
    workspace_id: WorkspaceId,
    run_id: UUID,
    out_dir: Path,
) -> int | None:
    """Files written, or None when the run is unknown or not succeeded."""
    reports = ReportRepo(pool)
    run = reports.run(workspace_id=workspace_id, run_id=run_id)
    if run is None or run["status"] != "succeeded":
        return None
    churn = ReplayRepo(pool).churn_facts(workspace_id=workspace_id)
    by_client: dict[UUID, list[dict[str, Any]]] = {}
    for row in reports.rows(workspace_id=workspace_id, run_id=run_id):
        by_client.setdefault(row["client_id"], []).append(row)
    stored_narratives = reports.narratives(workspace_id=workspace_id, run_id=run_id)
    narratives: list[tuple[Narrative, dict[str, Any]]] = []
    for client_id, raw in by_client.items():
        churned_at, notice_at, first_known_at = churn.get(client_id, (None, None, None))
        narratives.append(
            (
                narrative_from_json(stored_narratives[client_id], client_name=raw[0]["name"])
                if client_id in stored_narratives
                else build_narrative(
                    _rows(raw),
                    client_name=raw[0]["name"],
                    status=raw[0]["status"],
                    churned_at=churned_at,
                    notice_at=notice_at,
                    first_known_at=first_known_at,
                ),
                raw[0],
            )
        )
    ids = list(
        {
            e
            for n, _ in narratives
            for d in (
                *n.visible_at_detection,
                *n.detection_timing,
                *(d for t in n.transitions for d in t.active_drivers),
                *(event.driver for event in n.events if event.driver),
            )
            for e in d.evidence_ids
        }
    )
    stored = reports.evidence(workspace_id=workspace_id, ids=ids)
    quotes = {
        i: QuoteView(
            quote=e["quote"],
            occurred_at=e["occurred_at"].date() if e["occurred_at"] else None,
            deep_link=e["deep_link"],
            author_name=e["author_name"],
            author_role=e["author_role"],
            subject=e["subject"],
            source_ref=e["source_ref"],
        )
        for i, e in stored.items()
    }
    window: tuple[date, date] = (run["window_start"], run["window_end"])
    out_dir.mkdir(parents=True, exist_ok=True)
    names: dict[str, int] = {}
    ordered = sorted(narratives, key=lambda p: p[0].client_name)
    filenames: list[str] = []
    for narrative, first in ordered:
        slug = _slug(narrative.client_name)
        names[slug] = names.get(slug, 0) + 1
        suffix = f"-{names[slug]}" if names[slug] > 1 else ""
        text = render_client(
            narrative, quotes, window=window, metadata_only=first["policy"] == "metadata_only"
        )
        (out_dir / f"{slug}{suffix}.md").write_text(text)
        filenames.append(f"{slug}{suffix}.md")
    index = render_index(
        [n for n, _ in ordered],
        window=window,
        totals=(run["summary"] or {}).get("totals", {}),
        filenames=filenames,
    )
    (out_dir / "index.md").write_text(index)
    return len(narratives) + 1
