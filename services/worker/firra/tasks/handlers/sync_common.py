"""Helpers shared by the provider sync handlers (Google, Microsoft)."""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from firra.db.connections import ConnectionRow
from firra.db.jobs import ClaimedJob
from firra.ingest.pipeline import Ingestor, IngestStats
from firra.tasks.context import TaskContext
from firra.tasks.errors import ContinueTask, PermanentTaskError


def load_connection(ctx: TaskContext, job: ClaimedJob, connection_id: Any) -> ConnectionRow:
    connection = ctx.connections.get(workspace_id=job.workspace_id, connection_id=connection_id)
    if connection is None or connection.status == "revoked":
        raise PermanentTaskError("connection_unavailable")
    return connection


def revoked(ctx: TaskContext, connection: ConnectionRow) -> PermanentTaskError:
    """Marks the connection revoked (the UI asks to reconnect) and returns the error to raise."""
    ctx.connections.mark_failed(
        workspace_id=connection.workspace_id,
        connection_id=connection.id,
        error_class="token_revoked",
        revoked=True,
    )
    return PermanentTaskError("token_revoked")


def new_ingestor(ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow) -> Ingestor:
    return Ingestor(
        workspace_id=job.workspace_id,
        connection_id=connection.id,
        directory=ctx.directory(job.workspace_id),
        records=ctx.records,
        raw=ctx.raw,
        stats=IngestStats.from_progress(job.progress),
    )


def save_cursor(ctx: TaskContext, connection: ConnectionRow, cursor: dict[str, Any]) -> None:
    ctx.connections.mark_synced(
        workspace_id=connection.workspace_id, connection_id=connection.id, cursor=cursor
    )


def paged_backfill(
    ctx: TaskContext,
    job: ClaimedJob,
    ingestor: Ingestor,
    fetch_page: Callable[[str | None], str | None],
) -> dict[str, Any]:
    """Runs ``fetch_page`` (ingests one page, returns the next token) within the time budget."""
    deadline = time.monotonic() + ctx.time_budget_seconds
    page_token: str | None = job.progress.get("page_token")
    while True:
        page_token = fetch_page(page_token)
        progress = {**ingestor.stats.as_progress(), "page_token": page_token}
        if not page_token:
            return {**progress, "completed_at": datetime.now(UTC).isoformat()}
        if time.monotonic() >= deadline:
            raise ContinueTask(progress)
        ctx.jobs.update_progress(workspace_id=job.workspace_id, job_id=job.id, progress=progress)


def month_windows(now: datetime, months: int) -> list[tuple[datetime, datetime]]:
    """Cloud backfill.ts semantics: calendar months, newest first, current month ending now."""
    windows: list[tuple[datetime, datetime]] = []
    for offset in range(months):
        absolute = now.year * 12 + now.month - 1 - offset
        start = datetime(absolute // 12, absolute % 12 + 1, 1, tzinfo=UTC)
        end = now if offset == 0 else windows[-1][0]
        windows.append((start, end))
    return windows
