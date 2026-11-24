"""Microsoft 365 sync (Outlook mail, Outlook calendar) for sync.backfill_chunk and sync.incremental.

Mail: backfill lists one received-time window across the mailbox; incremental follows a
receivedDateTime watermark with a small overlap (Graph message delta is per folder, so a mailbox
wide listing replaces it; upserts make the overlap harmless). Calendar: backfill lists the
calendarView of the window; incremental runs calendarView delta rounds over [now - 1 day,
now + 365 days], re-anchored monthly or when Graph expires the delta state.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from firra.connectors.errors import TokenRevokedError
from firra.connectors.microsoft.auth import GraphAccess
from firra.connectors.microsoft.calendar import PREFER as CALENDAR_PREFER
from firra.connectors.microsoft.calendar import EventPage, OutlookCalendarClient
from firra.connectors.microsoft.credentials import CALENDAR_SCOPE, MAIL_SCOPE
from firra.connectors.microsoft.graph import (
    ConflictError,
    GraphClient,
    NotFoundError,
    SyncStateExpiredError,
)
from firra.connectors.microsoft.outlook import PREFER_TEXT, OutlookMailClient
from firra.connectors.microsoft.subscriptions import PushTarget, ensure_subscription
from firra.db.connections import ConnectionRow
from firra.db.jobs import ClaimedJob
from firra.http import ProviderError
from firra.ingest.pipeline import Ingestor
from firra.logging import get_logger
from firra.tasks.context import TaskContext
from firra.tasks.handlers.sync_common import new_ingestor, paged_backfill, revoked, save_cursor
from firra_contracts import SyncBackfillChunkPayload, SyncIncrementalPayload

log = get_logger(__name__)
MAIL_OVERLAP = timedelta(minutes=10)
CALENDAR_LOOKBACK = timedelta(days=1)
CALENDAR_HORIZON = timedelta(days=365)
CALENDAR_REANCHOR = timedelta(days=30)


def _renew_subscription(
    ctx: TaskContext,
    connection: ConnectionRow,
    graph: GraphClient,
    collection: str,
    *,
    force: bool,
) -> dict[str, Any]:
    """Keeps a Graph change-notification subscription for this connection alive."""
    if not ctx.microsoft_notification_url:
        return {}
    target = PushTarget(
        notification_url=ctx.microsoft_notification_url,
        workspace_id=connection.workspace_id,
        connection_id=connection.id,
        collection=collection,
    )
    try:
        push = ensure_subscription(
            graph, target, connection.cursor.get("push"), now=datetime.now(UTC), force=force
        )
    except (ProviderError, ConflictError, NotFoundError) as exc:
        # The sweep keeps syncing without push; try again on the next run.
        status = exc.status if isinstance(exc, ProviderError) else None
        log.warning("push.renew_failed", provider=connection.provider, status=status)
        return {"push_renewed": False}
    if push is None:
        return {}
    save_cursor(ctx, connection, {"push": push})
    return {"push_renewed": True}


def _graph(
    ctx: TaskContext, connection: ConnectionRow, scope: str, prefer: tuple[str, ...]
) -> GraphClient:
    try:
        access: GraphAccess = ctx.microsoft.access(connection, scope)
    except TokenRevokedError as exc:
        raise revoked(ctx, connection) from exc
    return GraphClient(ctx.http, access.access_token, access.user_path, prefer)


def _mail(ctx: TaskContext, connection: ConnectionRow) -> OutlookMailClient:
    return OutlookMailClient(_graph(ctx, connection, MAIL_SCOPE, (PREFER_TEXT,)))


def _calendar(ctx: TaskContext, connection: ConnectionRow) -> OutlookCalendarClient:
    return OutlookCalendarClient(_graph(ctx, connection, CALENDAR_SCOPE, CALENDAR_PREFER))


# --- Outlook mail --------------------------------------------------------------------------


def outlook_backfill(
    ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow, payload: SyncBackfillChunkPayload
) -> dict[str, Any]:
    mail = _mail(ctx, connection)
    excluded = mail.excluded_folder_ids()
    ingestor = new_ingestor(ctx, job, connection)

    def fetch_page(next_link: str | None) -> str | None:
        page = mail.page(payload.window_start, payload.window_end, next_link, excluded)
        ingestor.stats.invalid += page.skipped
        for message in page.messages:
            ingestor.message(message)
        return page.next_link

    return paged_backfill(ctx, job, ingestor, fetch_page)


def outlook_incremental(
    ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow, payload: SyncIncrementalPayload
) -> dict[str, Any]:
    mail = _mail(ctx, connection)
    result = _outlook_watermark(ctx, job, connection, mail)
    push = _renew_subscription(
        ctx, connection, mail.graph, "messages", force=bool(payload.renew_push)
    )
    return {**result, **push}


def _outlook_watermark(
    ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow, mail: OutlookMailClient
) -> dict[str, Any]:
    now = datetime.now(UTC)
    stored = connection.cursor.get("received_after")
    if not stored:
        # First sync after connect: start now; the backfill covers the past.
        save_cursor(ctx, connection, {"received_after": now.isoformat()})
        return {"initialized": True}

    watermark = datetime.fromisoformat(str(stored))
    excluded = mail.excluded_folder_ids()
    ingestor = new_ingestor(ctx, job, connection)
    next_link: str | None = None
    while True:
        page = mail.page(watermark - MAIL_OVERLAP, None, next_link, excluded)
        ingestor.stats.invalid += page.skipped
        for message in page.messages:
            ingestor.message(message)
        if page.latest_received and page.latest_received > watermark:
            watermark = min(page.latest_received, now)
        next_link = page.next_link
        if not next_link:
            break
    save_cursor(ctx, connection, {"received_after": watermark.isoformat()})
    return ingestor.stats.as_progress()


# --- Outlook calendar ----------------------------------------------------------------------


def _ingest_events(
    ctx: TaskContext,
    connection: ConnectionRow,
    ingestor: Ingestor,
    page: EventPage,
    *,
    now: datetime,
    horizon: datetime,
) -> int:
    ingestor.stats.invalid += page.skipped
    for event in page.events:
        ingestor.calendar_event(event, now=now)
    cancelled = 0
    # @removed also covers events changed outside the window, so only stored meetings that were
    # still ahead and inside the window count as cancelled.
    for external_id in page.removed:
        cancelled += ctx.records.cancel_upcoming_event(
            workspace_id=connection.workspace_id,
            connection_id=connection.id,
            external_id=external_id,
            after=now,
            before=horizon,
        )
    return cancelled


def outlook_calendar_backfill(
    ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow, payload: SyncBackfillChunkPayload
) -> dict[str, Any]:
    calendar = _calendar(ctx, connection)
    ingestor = new_ingestor(ctx, job, connection)
    now = datetime.now(UTC)

    def fetch_page(next_link: str | None) -> str | None:
        page = calendar.window(payload.window_start, payload.window_end, next_link)
        _ingest_events(ctx, connection, ingestor, page, now=now, horizon=now)
        return page.next_link

    return paged_backfill(ctx, job, ingestor, fetch_page)


def outlook_calendar_incremental(
    ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow, payload: SyncIncrementalPayload
) -> dict[str, Any]:
    calendar = _calendar(ctx, connection)
    result = _calendar_delta(ctx, job, connection, calendar)
    push = _renew_subscription(
        ctx, connection, calendar.graph, "events", force=bool(payload.renew_push)
    )
    return {**result, **push}


def _calendar_delta(
    ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow, calendar: OutlookCalendarClient
) -> dict[str, Any]:
    ingestor = new_ingestor(ctx, job, connection)
    now = datetime.now(UTC)
    cursor = connection.cursor
    link: str | None = cursor.get("delta_link")
    start = datetime.fromisoformat(cursor["window_start"]) if link else now - CALENDAR_LOOKBACK
    end = datetime.fromisoformat(cursor["window_end"]) if link else now + CALENDAR_HORIZON
    reset = False
    if link and now - start > CALENDAR_REANCHOR + CALENDAR_LOOKBACK:
        link, start, end = None, now - CALENDAR_LOOKBACK, now + CALENDAR_HORIZON

    cancelled = 0
    delta_link: str | None = None
    while True:
        try:
            page = calendar.delta(link, start, end)
        except SyncStateExpiredError:
            if reset:
                raise
            link, start, end, reset = None, now - CALENDAR_LOOKBACK, now + CALENDAR_HORIZON, True
            continue
        cancelled += _ingest_events(ctx, connection, ingestor, page, now=now, horizon=end)
        link, delta_link = page.next_link, page.delta_link
        if link is None:
            break  # without a delta link the next run simply starts a new round
    cursor = {"delta_link": delta_link, "window_start": start.isoformat()}
    save_cursor(ctx, connection, {**cursor, "window_end": end.isoformat()})
    return {**ingestor.stats.as_progress(), "cancelled": cancelled, "sync_reset": reset}
