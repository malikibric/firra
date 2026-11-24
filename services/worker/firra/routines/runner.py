"""Run a first-party source through the deterministic engine; no model calls."""

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Protocol
from uuid import UUID

from firra.db.routines import RoutinesRepo
from firra.db.types import WorkspaceId
from firra.routines.config import load_config
from firra.routines.engine import plan
from firra.routines.model import Candidate
from firra.routines.sources.commitment_watch import CommitmentWatch
from firra.routines.sources.morning_scan import MorningScan


class RoutineSource(Protocol):
    def candidates(
        self, repo: RoutinesRepo, *, workspace_id: WorkspaceId, as_of: date
    ) -> Sequence[Candidate]: ...


SOURCES: dict[str, RoutineSource] = {
    "morning_scan": MorningScan(),
    "commitment_watch": CommitmentWatch(),
}


def run(
    repo: RoutinesRepo, *, workspace_id: WorkspaceId, routine_id: UUID, scheduled_for: date
) -> dict[str, int]:
    claimed = repo.claim_run(
        workspace_id=workspace_id, routine_id=routine_id, scheduled_for=scheduled_for
    )
    if claimed is None:
        return {"not_claimed": 1}
    run_id = claimed["id"]
    try:
        source = SOURCES.get(claimed["kind"])
        if not claimed["enabled"]:
            counts = {"disabled": 1}
        elif source is None:
            counts = {"items": 0, "no_source": 1}
        else:
            candidates = source.candidates(repo, workspace_id=workspace_id, as_of=scheduled_for)
            client_ids = sorted({c.client_id for c in candidates})
            contexts = repo.contexts(
                workspace_id=workspace_id, client_ids=client_ids, as_of=scheduled_for
            )
            # Contexts omit excluded and out-of-workspace clients before prior reads and planning.
            grouped: dict[UUID, list[Candidate]] = {client_id: [] for client_id in contexts}
            for candidate in candidates:
                if candidate.client_id in grouped:
                    grouped[candidate.client_id].append(candidate)
            week = scheduled_for - timedelta(days=scheduled_for.weekday())
            planned = plan(
                grouped,
                contexts,
                repo.priors(workspace_id=workspace_id, client_ids=list(contexts)),
                repo.important_used(workspace_id=workspace_id, week_start=week),
                load_config(),
                scheduled_for,
            )
            saved = repo.write_cards(
                workspace_id=workspace_id,
                decisions=planned.decisions,
                run_id=run_id,
                as_of=scheduled_for,
            )
            counts = {**planned.counts, "skipped_no_assessment": saved["skipped_no_assessment"]}
        repo.finish_run(workspace_id=workspace_id, run_id=run_id, status="succeeded", counts=counts)
        return counts
    except Exception as error:
        repo.finish_run(
            workspace_id=workspace_id,
            run_id=run_id,
            status="failed",
            counts={},
            error_class=type(error).__name__,
        )
        raise
