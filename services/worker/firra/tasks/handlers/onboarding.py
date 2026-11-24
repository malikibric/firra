"""onboarding.suggest: rank the outside domains the owner talks to (headers only, no storage)."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from firra.connectors.errors import TokenRevokedError
from firra.connectors.google import limits
from firra.connectors.google.credentials import GMAIL_SCOPE
from firra.connectors.google.gmail import EXCLUDE, GmailClient
from firra.connectors.google.suggest import suggest_domains
from firra.db.jobs import ClaimedJob
from firra.tasks.context import TaskContext
from firra.tasks.errors import PermanentTaskError
from firra.tasks.handlers.sync_common import load_connection, revoked
from firra_contracts import Message, OnboardingSuggestPayload

MAX_MESSAGES = 1500
PARALLEL_READS = 6
PROGRESS_EVERY = 60
SUGGEST_BUDGET_SECONDS = 90.0


def handle_onboarding_suggest(
    ctx: TaskContext, job: ClaimedJob, payload: OnboardingSuggestPayload
) -> dict[str, Any]:
    connection = load_connection(ctx, job, payload.connection_id)
    if connection.provider != "gmail":
        raise PermanentTaskError("unsupported_connection")
    try:
        token = ctx.google.access_token(connection, [GMAIL_SCOPE])
    except TokenRevokedError as exc:
        raise revoked(ctx, connection) from exc
    client = GmailClient(ctx.http, token, limits.Pacer(limits.GMAIL_SCAN_INTERVAL))
    since = datetime.now(UTC) - timedelta(days=30 * max(connection.backfill_months, 1))
    query = f"after:{int(since.timestamp())} {EXCLUDE}"
    # Onboarding waits on this: read headers in parallel (Gmail allows ~50 metadata reads a second
    # per user), stop after a short budget and rank what was read rather than make the owner wait.
    deadline = time.monotonic() + min(ctx.time_budget_seconds, SUGGEST_BUDGET_SECONDS)
    messages: list[Message] = []
    page_token: str | None = None

    def fetch(message_id: str) -> Message | None:
        if time.monotonic() >= deadline:
            return None
        return client.message(message_id, headers_only=True, not_found_ok=True)

    with ThreadPoolExecutor(max_workers=PARALLEL_READS) as pool:
        while len(messages) < MAX_MESSAGES and time.monotonic() < deadline:
            page = client.list_ids(query, page_token)
            ids = page.ids[: MAX_MESSAGES - len(messages)]
            for start in range(0, len(ids), PROGRESS_EVERY):
                batch = ids[start : start + PROGRESS_EVERY]
                messages.extend(m for m in pool.map(fetch, batch) if m is not None)
                ctx.jobs.update_progress(
                    workspace_id=job.workspace_id,
                    job_id=job.id,
                    progress={"scanned": len(messages)},
                )
                if time.monotonic() >= deadline:
                    break
            page_token = page.next_page_token
            if not page_token:
                break
    found = suggest_domains(messages, own_addresses=[connection.account_label])
    return {
        "scanned": len(messages),
        "suggestions": [
            {
                "domain": s.domain,
                "thread_count": s.thread_count,
                "message_count": s.message_count,
                "last_seen": s.last_seen.isoformat(),
                "sample_names": list(s.sample_names),
            }
            for s in found
        ],
    }
