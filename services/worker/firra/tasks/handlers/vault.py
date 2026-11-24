"""Fetch known, purged email for its owner's vault without ingest or extraction.

Docs checked (2026-10-02): Gmail users.messages.get format=raw;
https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get
Graph GET /me/messages/{id}, $select and text body preference;
https://learn.microsoft.com/en-us/graph/api/message-get?view=graph-rest-1.0
"""

import hashlib
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from firra.clean import clean_email
from firra.connectors.google.gmail import GmailClient
from firra.connectors.google.limits import throttled
from firra.connectors.microsoft.graph import NotFoundError
from firra.db.jobs import ClaimedJob
from firra.db.vault import VaultCandidate, VaultRepo
from firra.http import ProviderError
from firra.logging import get_logger
from firra.tasks.context import TaskContext
from firra.tasks.errors import ContinueTask, PermanentTaskError
from firra.tasks.handlers.sync import THROTTLED_WAIT_SECONDS
from firra.tasks.handlers.sync_common import revoked
from firra.tasks.handlers.sync_google import _access_token
from firra.tasks.handlers.sync_microsoft import _mail
from firra_contracts import Message
from firra_contracts.generated.tasks.vault_rehydrate import VaultRehydratePayload

log = get_logger(__name__)
COUNTERS = ("total", "done", "missing", "changed", "failed")


def _body(
    fetch: Callable[[str], Message | None], record: VaultCandidate, progress: dict[str, Any]
) -> str | None:
    try:
        message = fetch(record.external_id)
    except NotFoundError:
        progress["missing"] += 1
        return None
    except ProviderError as error:
        if error.status != 404:
            raise
        progress["missing"] += 1
        return None
    if message is None:
        progress["failed"] += 1
        return None
    body = clean_email(message.body_text or "").body
    if hashlib.sha256(body.encode()).digest() != record.content_hash:
        progress["changed"] += 1
        return None
    return body


def handle_vault_rehydrate(
    ctx: TaskContext, job: ClaimedJob, payload: VaultRehydratePayload
) -> dict[str, Any]:
    deadline = time.monotonic() + ctx.time_budget_seconds
    repo = VaultRepo(ctx.pool)
    progress = {**job.progress, **{key: int(job.progress.get(key, 0)) for key in COUNTERS}}
    connection = ctx.connections.get(
        workspace_id=job.workspace_id, connection_id=payload.connection_id
    )
    if connection is None or not repo.authorized(
        workspace_id=job.workspace_id, connection_id=payload.connection_id, user_id=payload.user_id
    ):
        log.warning("vault.rehydrate_unavailable", error_class="connection_unavailable")
        return progress
    progress.setdefault("until", datetime.now(UTC).isoformat())
    params = {
        "workspace_id": job.workspace_id,
        "connection_id": connection.id,
        "user_id": payload.user_id,
        "until": progress["until"],
        "months": connection.backfill_months,
    }
    if "total" not in job.progress:
        progress["total"] = repo.count(workspace_id=job.workspace_id, params=params)
    repo.checkpoint(job=job, progress=progress, params=params)
    if not progress["total"]:
        return progress
    batch = repo.candidates(workspace_id=job.workspace_id, params=params, progress=progress)
    if not batch:
        return progress
    try:
        fetch: Callable[[str], Message | None]
        if connection.provider == "gmail":
            fetch = GmailClient(ctx.http, _access_token(ctx, connection)).message
        else:
            mail = _mail(ctx, connection)
            excluded = mail.excluded_folder_ids()

            def fetch(external_id: str) -> Message | None:
                return mail.message(external_id, excluded)

        while True:
            for record in batch:
                body = _body(fetch, record, progress)
                progress.update(cursor_at=record.occurred_at.isoformat(), cursor_id=str(record.id))
                repo.checkpoint(job=job, progress=progress, params=params, record=record, body=body)
                if time.monotonic() >= deadline:
                    raise ContinueTask(progress)
            batch = repo.candidates(workspace_id=job.workspace_id, params=params, progress=progress)
            if not batch:
                return progress
    except (ProviderError, PermanentTaskError) as error:
        if isinstance(error, ProviderError) and throttled(error):
            raise ContinueTask(progress, delay_seconds=THROTTLED_WAIT_SECONDS) from error
        progress["failed"] += 1
        repo.checkpoint(job=job, progress=progress, params=params)
        if isinstance(error, ProviderError) and error.status == 401:
            raise revoked(ctx, connection) from error
        raise
