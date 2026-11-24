"""Google sync (Gmail, Calendar) for sync.backfill_chunk and sync.incremental.

Backfill covers one window (a month) and continues itself when the time budget runs out; the resume
point (page token) and counters live in job.progress. Incremental sync follows the provider cursor
(Gmail historyId, Calendar syncToken) and restarts cleanly when the provider expires it.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from firra.connectors.errors import TokenRevokedError
from firra.connectors.google.calendar import CalendarClient, SyncTokenExpiredError
from firra.connectors.google.credentials import CALENDAR_SCOPE, GMAIL_SCOPE
from firra.connectors.google.gmail import GmailClient, HistoryExpiredError, window_query
from firra.connectors.google.local_tokens import LocalGoogleTokens
from firra.db.connections import ConnectionRow
from firra.db.jobs import ClaimedJob
from firra.http import ProviderError
from firra.ingest.pipeline import Ingestor
from firra.logging import get_logger
from firra.tasks.context import TaskContext
from firra.tasks.handlers.sync_common import (
    load_connection,
    month_windows,
    new_ingestor,
    paged_backfill,
    revoked,
    save_cursor,
)
from firra_contracts import SyncBackfillChunkPayload, SyncIncrementalPayload

log = get_logger(__name__)
WATCH_RENEWAL = timedelta(hours=24)
SCOPES = {"gmail": GMAIL_SCOPE, "google_calendar": CALENDAR_SCOPE}


def _access_token(ctx: TaskContext, connection: ConnectionRow) -> str:
    try:
        return ctx.google.access_token(connection, [SCOPES[connection.provider]])
    except TokenRevokedError as exc:
        raise revoked(ctx, connection) from exc


# --- Gmail ---------------------------------------------------------------------------------


def _ingest_gmail_ids(client: GmailClient, ingestor: Ingestor, ids: list[str]) -> None:
    """Headers first; the full message (with attachments) only for mail minimization may keep."""
    for message_id in ids:
        message = client.message(message_id, headers_only=True, not_found_ok=True)
        if message is not None and ingestor.needs_content(message):
            message = client.message(message_id, not_found_ok=True)
        if message is None:
            ingestor.stats.invalid += 1
        else:
            ingestor.message(message)


def gmail_backfill(
    ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow, payload: SyncBackfillChunkPayload
) -> dict[str, Any]:
    client = GmailClient(ctx.http, _access_token(ctx, connection))
    ingestor = new_ingestor(ctx, job, connection)
    # Only mail that names a mapped client is listed; with no client mapped yet nothing is read
    # (a mapping change re-reads the window, onboarding.read_mail_again).
    domains = sorted(ctx.directory(job.workspace_id).client_domains)
    query = window_query(payload.window_start, payload.window_end, domains)

    def fetch_page(page_token: str | None) -> str | None:
        if not domains:
            return None
        if isinstance(ctx.google, LocalGoogleTokens):
            load_connection(ctx, job, connection.id)
        page = client.list_ids(query, page_token)
        _ingest_gmail_ids(client, ingestor, page.ids)
        return page.next_page_token

    return paged_backfill(ctx, job, ingestor, fetch_page)


def gmail_incremental(
    ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow, payload: SyncIncrementalPayload
) -> dict[str, Any]:
    client = GmailClient(ctx.http, _access_token(ctx, connection))
    result = _gmail_history(ctx, job, connection, client)
    return {**result, **_renew_watch(ctx, connection, client, force=bool(payload.renew_push))}


def _renew_watch(
    ctx: TaskContext, connection: ConnectionRow, client: GmailClient, *, force: bool
) -> dict[str, Any]:
    """Keeps Gmail push notifications flowing: users.watch at most once a day (or when asked)."""
    if isinstance(ctx.google, LocalGoogleTokens) or not ctx.gmail_push_topic:
        return {}
    now = datetime.now(UTC)
    renewed_at = (connection.cursor.get("push") or {}).get("renewed_at")
    if not force and renewed_at and now - datetime.fromisoformat(renewed_at) < WATCH_RENEWAL:
        return {}
    try:
        expires_ms = client.watch(ctx.gmail_push_topic)
    except ProviderError as exc:
        # The sweep keeps syncing without push; try again on the next run.
        log.warning("push.renew_failed", provider="gmail", status=exc.status)
        return {"push_renewed": False}
    expires_at = datetime.fromtimestamp(expires_ms / 1000, UTC)
    save_cursor(
        ctx,
        connection,
        {"push": {"renewed_at": now.isoformat(), "expires_at": expires_at.isoformat()}},
    )
    return {"push_renewed": True}


def _gmail_history(
    ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow, client: GmailClient
) -> dict[str, Any]:
    if isinstance(ctx.google, LocalGoogleTokens) and connection.cursor.get("gmail_resync"):
        return _gmail_resync(ctx, job, connection, client)
    start = connection.cursor.get("history_id")
    if not start:
        # First sync after connect: history starts now; the backfill covers the past.
        save_cursor(ctx, connection, {"history_id": client.history_id()})
        return {"initialized": True}

    ingestor = new_ingestor(ctx, job, connection)
    deleted = 0
    latest = str(start)
    page_token: str | None = None
    try:
        while True:
            page = client.history(str(start), page_token)
            _ingest_gmail_ids(client, ingestor, page.added)
            for message_id in page.deleted:
                deleted += ctx.records.delete_record(
                    workspace_id=job.workspace_id,
                    connection_id=connection.id,
                    external_id=message_id,
                )
            latest = page.history_id or latest
            page_token = page.next_page_token
            if not page_token:
                break
    except HistoryExpiredError:
        if isinstance(ctx.google, LocalGoogleTokens):
            return _gmail_resync(ctx, job, connection, client)
        # Cursor older than Gmail keeps history: restart from now and report it.
        save_cursor(ctx, connection, {"history_id": client.history_id()})
        return {**ingestor.stats.as_progress(), "history_reset": True}

    save_cursor(ctx, connection, {"history_id": latest})
    return {**ingestor.stats.as_progress(), "deleted": deleted}


def _gmail_resync(
    ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow, client: GmailClient
) -> dict[str, Any]:
    """Capture the history watermark before the scan; resume the same window after a crash.

    Docs checked: 2026-10-05, https://developers.google.com/workspace/gmail/api/guides/sync
    (history 404 requires a full sync). Idempotent ingest preserves existing record identity.
    """
    state = connection.cursor.get("gmail_resync")
    if not state:
        now = datetime.now(UTC)
        windows = month_windows(now, connection.backfill_months)
        state = {
            "history_id": client.history_id(),
            "window_start": (windows[-1][0] if windows else now).isoformat(),
            "window_end": now.isoformat(),
        }
        save_cursor(ctx, connection, {"gmail_resync": state})
    domains = sorted(ctx.directory(job.workspace_id).client_domains)
    query = window_query(
        datetime.fromisoformat(state["window_start"]),
        datetime.fromisoformat(state["window_end"]),
        domains,
    )
    ingestor = new_ingestor(ctx, job, connection)

    def fetch_page(page_token: str | None) -> str | None:
        if not domains:
            return None
        load_connection(ctx, job, connection.id)
        page = client.list_ids(query, page_token)
        _ingest_gmail_ids(client, ingestor, page.ids)
        return page.next_page_token

    result = paged_backfill(ctx, job, ingestor, fetch_page)
    save_cursor(ctx, connection, {"history_id": state["history_id"], "gmail_resync": None})
    return {**result, "history_reset": True}


# --- Google Calendar ----------------------------------------------------------------------


def calendar_backfill(
    ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow, payload: SyncBackfillChunkPayload
) -> dict[str, Any]:
    client = CalendarClient(ctx.http, _access_token(ctx, connection))
    ingestor = new_ingestor(ctx, job, connection)
    now = datetime.now(UTC)

    def fetch_page(page_token: str | None) -> str | None:
        if isinstance(ctx.google, LocalGoogleTokens):
            load_connection(ctx, job, connection.id)
        page = client.window(payload.window_start, payload.window_end, page_token)
        ingestor.stats.invalid += page.skipped
        for event in page.events:
            ingestor.calendar_event(event, now=now)
        return page.next_page_token

    return paged_backfill(ctx, job, ingestor, fetch_page)


def calendar_incremental(
    ctx: TaskContext, job: ClaimedJob, connection: ConnectionRow, _payload: SyncIncrementalPayload
) -> dict[str, Any]:
    client = CalendarClient(ctx.http, _access_token(ctx, connection))
    ingestor = new_ingestor(ctx, job, connection)
    now = datetime.now(UTC)
    sync_token: str | None = connection.cursor.get("sync_token")
    local = isinstance(ctx.google, LocalGoogleTokens)
    windows = month_windows(now, connection.backfill_months) if local else []
    since = windows[-1][0] if windows else now
    reset = False
    page_token: str | None = None
    while True:
        try:
            if local:
                load_connection(ctx, job, connection.id)
            page = client.changes(sync_token, page_token, since=since)
        except SyncTokenExpiredError:
            if local:
                if sync_token is None:
                    raise ProviderError(410) from None
                save_cursor(ctx, connection, {"sync_token": None})
            # Expired token: start over from upcoming events (past months come from backfill).
            sync_token, page_token, reset = None, None, True
            continue
        ingestor.stats.invalid += page.skipped
        for event in page.events:
            ingestor.calendar_event(event, now=now)
        page_token = page.next_page_token
        if not page_token:
            break
    save_cursor(ctx, connection, {"sync_token": page.next_sync_token})
    return {**ingestor.stats.as_progress(), "sync_reset": reset}
