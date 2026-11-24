"""ingest.upload: parse an uploaded .mbox/.eml file and ingest its messages.

Idempotent: records upsert on (connection_id, external_id), so a retried task converges. Large
mbox files continue in follow-up tasks when the time budget runs out; job.progress keeps the
number of messages already handled (``offset``) and the counters.
"""

import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from firra.connectors.email_files import iter_mbox, parse_message
from firra.db.jobs import ClaimedJob
from firra.ingest.pipeline import Ingestor, IngestStats
from firra.storage.raw import ObjectNotFoundError
from firra.tasks.context import TaskContext
from firra.tasks.errors import ContinueTask, PermanentTaskError
from firra.tasks.handlers.process import request_processing
from firra_contracts import IngestUploadPayload

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
PROGRESS_EVERY = 500


def handle_ingest_upload(
    ctx: TaskContext, job: ClaimedJob, payload: IngestUploadPayload
) -> dict[str, Any]:
    connection = ctx.connections.get(
        workspace_id=job.workspace_id, connection_id=payload.connection_id
    )
    if connection is None or connection.mode != "upload" or connection.status == "revoked":
        raise PermanentTaskError("connection_unavailable")
    # The object must live under this workspace's and connection's upload prefix.
    expected = f"/uploads/{job.workspace_id}/{connection.id}/"
    if expected not in payload.object_uri:
        raise PermanentTaskError("invalid_object")

    try:
        data = ctx.raw.get(payload.object_uri)
    except ObjectNotFoundError as exc:
        raise PermanentTaskError("object_missing") from exc
    if len(data) > MAX_UPLOAD_BYTES:
        raise PermanentTaskError("upload_too_large")

    ingestor = Ingestor(
        workspace_id=job.workspace_id,
        connection_id=connection.id,
        directory=ctx.directory(job.workspace_id),
        records=ctx.records,
        raw=ctx.raw,
        stats=IngestStats.from_progress(job.progress),
    )
    offset = int(job.progress.get("offset", 0))
    deadline = time.monotonic() + ctx.time_budget_seconds

    def ingest(raw: bytes) -> None:
        message = parse_message(raw)
        if message is None:
            ingestor.stats.invalid += 1
        else:
            ingestor.message(message)
        if ingestor.stats.processed % PROGRESS_EVERY == 0:
            ctx.jobs.update_progress(
                workspace_id=job.workspace_id, job_id=job.id, progress=ingestor.stats.as_progress()
            )

    if payload.format.value == "eml":
        ingest(data)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "upload.mbox"
            path.write_bytes(data)
            for index, raw in enumerate(iter_mbox(path)):
                if index < offset:
                    continue  # handled by an earlier continuation of this job
                if index > offset and time.monotonic() >= deadline:
                    raise ContinueTask({**ingestor.stats.as_progress(), "offset": index})
                ingest(raw)

    ctx.connections.mark_synced(
        workspace_id=job.workspace_id,
        connection_id=connection.id,
        cursor={"last_upload_at": datetime.now(UTC).isoformat()},
    )
    request_processing(ctx, job.workspace_id, "ingest")
    return {**ingestor.stats.as_progress(), "offset": None}
