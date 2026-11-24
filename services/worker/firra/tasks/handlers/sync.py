"""sync.backfill_chunk and sync.incremental: dispatch to the provider's sync implementation."""

from collections.abc import Callable
from typing import Any

from firra.connectors.google.limits import throttled
from firra.db.connections import ConnectionRow
from firra.db.jobs import ClaimedJob
from firra.http import ProviderError
from firra.tasks.context import TaskContext
from firra.tasks.errors import ContinueTask, PermanentTaskError
from firra.tasks.handlers import sync_google, sync_microsoft
from firra.tasks.handlers.process import request_processing
from firra.tasks.handlers.sync_common import load_connection
from firra_contracts import SyncBackfillChunkPayload, SyncIncrementalPayload

Backfill = Callable[
    [TaskContext, ClaimedJob, ConnectionRow, SyncBackfillChunkPayload], dict[str, Any]
]
Incremental = Callable[
    [TaskContext, ClaimedJob, ConnectionRow, SyncIncrementalPayload], dict[str, Any]
]

# Month chunks of one mailbox run at most two at a time: providers throttle per mailbox (Gmail
# answers parallel requests with 403 rateLimitExceeded). Waiting chunks look again after a minute.
MAX_PARALLEL_BACKFILLS = 2
WAIT_SECONDS = 60
# A mailbox still throttled after the request retries is left alone for a while; the chunk resumes
# from its last saved page without using an attempt (a throttled mailbox is not a broken job).
THROTTLED_WAIT_SECONDS = 300

BACKFILL: dict[str, Backfill] = {
    "gmail": sync_google.gmail_backfill,
    "google_calendar": sync_google.calendar_backfill,
    "outlook": sync_microsoft.outlook_backfill,
    "outlook_calendar": sync_microsoft.outlook_calendar_backfill,
}
INCREMENTAL: dict[str, Incremental] = {
    "gmail": sync_google.gmail_incremental,
    "google_calendar": sync_google.calendar_incremental,
    "outlook": sync_microsoft.outlook_incremental,
    "outlook_calendar": sync_microsoft.outlook_calendar_incremental,
}


def handle_backfill_chunk(
    ctx: TaskContext, job: ClaimedJob, payload: SyncBackfillChunkPayload
) -> dict[str, Any]:
    connection = load_connection(ctx, job, payload.connection_id)
    handler = BACKFILL.get(connection.provider)
    if handler is None:
        raise PermanentTaskError("unsupported_provider")
    ahead = ctx.jobs.running_ahead(
        workspace_id=job.workspace_id, job_id=job.id, connection_id=connection.id
    )
    if ahead >= MAX_PARALLEL_BACKFILLS:
        raise ContinueTask({}, delay_seconds=WAIT_SECONDS)
    try:
        progress = handler(ctx, job, connection, payload)
    except ProviderError as exc:
        if not throttled(exc):
            raise
        raise ContinueTask({}, delay_seconds=THROTTLED_WAIT_SECONDS) from exc
    request_processing(ctx, job.workspace_id, "ingest")
    return progress


def handle_incremental(
    ctx: TaskContext, job: ClaimedJob, payload: SyncIncrementalPayload
) -> dict[str, Any]:
    connection = load_connection(ctx, job, payload.connection_id)
    handler = INCREMENTAL.get(connection.provider)
    if handler is None:
        raise PermanentTaskError("unsupported_provider")
    progress = handler(ctx, job, connection, payload)
    request_processing(ctx, job.workspace_id, "ingest")
    return progress
