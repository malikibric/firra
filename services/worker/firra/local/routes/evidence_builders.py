"""Pure evidence and replay shaping, matching read-store.ts and generated contracts."""

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from firra.local.bodycrypt import read_field
from firra.local.paging import instant
from firra_contracts.generated_v1.evidence_item import EvidenceItem
from firra_contracts.generated_v1.replay_client_response import Narrative, ReplayClientResponse
from firra_contracts.generated_v1.replay_runs_response import Item as ReplayRun


def shape_evidence(
    row: dict[str, Any], *, pool: object | None = None, workspace_id: object = None
) -> dict[str, Any]:
    """With ``pool`` the encrypted mail subject is decrypted (local profile, source-layer S0)."""
    quote = row["quote"]
    if not 3 <= len(re.split(r"\s+", quote.strip())) <= 25:
        raise ValueError("invalid evidence quote")
    subject = row["metadata"].get("subject")
    if pool is not None:
        subject = read_field(pool, workspace_id, row["source_record_id"], "subject", subject)
    body = {key: row[key] for key in ("id", "client_id", "quote", "locator", "deep_link")}
    body.update(
        occurred_at=instant(row["occurred_at"]),
        source_ref=str(row["source_record_id"])[:8],
        subject=subject.strip()[:120] if subject is not None else None,
        author_name=row["display_name"] if row["side"] == "client" else None,
        author_role=row["role_title"] if row["side"] == "client" else None,
    )
    return EvidenceItem.model_validate(body).model_dump(mode="json")


def shape_run(row: dict[str, Any]) -> dict[str, Any]:
    body = {
        key: row[key]
        for key in ("id", "window_start", "window_end", "step_days", "status", "finished_at")
    }
    body.update(
        totals=(row["summary"] or {}).get("totals"),
        narrated=row["status"] == "succeeded" and row["narrated"],
    )
    return ReplayRun.model_validate(body, extra="ignore").model_dump(mode="json")


def shape_replay(
    narrative: dict[str, Any],
    weeks: list[dict[str, Any]],
    *,
    policy: str,
    run_id: UUID,
    client_id: UUID,
    now: datetime,
) -> dict[str, Any] | None:
    stored = Narrative.model_validate(narrative, extra="ignore").model_dump(
        mode="json", exclude_unset=True
    )
    drivers = [
        *stored["visible_at_detection"],
        *stored["detection_timing"],
        *[d for t in stored["transitions"] for d in [*t["new_drivers"], *t["active_drivers"]]],
        *[e["driver"] for e in stored["events"] if e["driver"]],
    ]
    if policy == "metadata_only" and (
        any(d["basis"] != "metadata" or d["evidence_ids"] for d in drivers)
        or any(e["grouping_ids"] for e in stored["events"])
    ):
        return None
    ids = list(
        dict.fromkeys(
            [i for d in drivers for i in d["evidence_ids"]]
            + [i for e in stored["events"] for i in e["grouping_ids"]]
        )
    )
    return ReplayClientResponse.model_validate(
        dict(
            generated_at=now,
            run_id=run_id,
            client_id=client_id,
            version=1,
            narrative=stored,
            weeks=weeks,
            evidence_ids=ids,
        )
    ).model_dump(mode="json", exclude_unset=True)
