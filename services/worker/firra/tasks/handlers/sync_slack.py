"""Local Slack sync with persisted pages, delayed throttling and revoked-token events."""

from typing import Any

from firra.connectors.errors import TokenRevokedError
from firra.connectors.slack.client import SlackClient, SlackRateLimitError
from firra.connectors.slack.sync import SlackSync
from firra.db.jobs import ClaimedJob
from firra.db.local_slack import SlackRepo
from firra.http import ProviderError
from firra.tasks.context import TaskContext
from firra.tasks.errors import ContinueTask, PermanentTaskError
from firra.tasks.handlers.process import request_processing
from firra.tasks.handlers.sync_common import load_connection, revoked
from firra_contracts import SyncSlackPayload


def handle_sync_slack(
    ctx: TaskContext, job: ClaimedJob, payload: SyncSlackPayload
) -> dict[str, Any]:
    connection = load_connection(ctx, job, payload.connection_id)
    if connection.provider != "slack" or connection.mode != "user":
        raise PermanentTaskError("unsupported_provider")
    if ctx.slack is None:
        raise PermanentTaskError("slack_not_configured")
    if (payload.window_start is None) != (payload.window_end is None) or (
        payload.window_start is not None
        and payload.window_end is not None
        and payload.window_start >= payload.window_end
    ):
        raise PermanentTaskError("invalid_sync_window")
    sync = None
    try:
        with SlackRepo(ctx.pool).syncing(
            workspace_id=job.workspace_id, connection_id=connection.id
        ) as acquired:
            if not acquired:
                raise ContinueTask(dict(job.progress), delay_seconds=5)
            client = SlackClient(
                ctx.http,
                ctx.slack.access_token(connection.id),
                workspace_url=connection.cursor.get("workspace_url"),
            )
            sync = SlackSync(ctx, job, connection, client, payload)
            result = sync.run()
    except SlackRateLimitError as exc:
        raise ContinueTask(
            sync.progress if sync else dict(job.progress), delay_seconds=exc.delay_seconds
        ) from None
    except TokenRevokedError:
        ctx.slack.on_revoked(connection)
        raise revoked(ctx, connection) from None
    except ProviderError as exc:
        if exc.reason == "missing_scope":
            ctx.connections.mark_failed(
                workspace_id=job.workspace_id,
                connection_id=connection.id,
                error_class="missing_scope",
                revoked=False,
            )
            raise PermanentTaskError("missing_scope") from None
        raise
    request_processing(ctx, job.workspace_id, "ingest")
    return result
