"""Polling mapped Slack channels and client DMs, with resumable page checkpoints.

Docs checked: 2026-10-05, https://docs.slack.dev/messaging/retrieving-messages/:
latest_reply is on the parent; history timestamps do not become reply timestamps.
Known parents are refreshed before comparing latest_reply. Daily rescans fetch all replies
for parents in the seven-day window and delete only after a complete authoritative scan.
"""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from firra.connectors.slack.client import SlackClient
from firra.connectors.slack.ingest import mapped_channels, store_message
from firra.db.connections import ConnectionRow
from firra.db.jobs import ClaimedJob
from firra.db.local_slack import SlackRepo
from firra.http import ProviderError
from firra.tasks.context import TaskContext
from firra.tasks.errors import ContinueTask, PermanentTaskError
from firra.tasks.handlers.sync_common import load_connection, month_windows
from firra_contracts import SyncSlackPayload, TaskEnvelope


class SlackSync:
    def __init__(
        self,
        ctx: TaskContext,
        job: ClaimedJob,
        connection: ConnectionRow,
        client: SlackClient,
        payload: SyncSlackPayload,
    ) -> None:
        self.ctx, self.job, self.connection, self.client, self.payload = (
            ctx,
            job,
            connection,
            client,
            payload,
        )
        self.repo = SlackRepo(ctx.pool)
        self.progress = dict(job.progress)
        self.deadline = time.monotonic() + ctx.time_budget_seconds
        self.now = datetime.fromisoformat(
            self.progress.setdefault("until", datetime.now(UTC).isoformat())
        )
        self.users = {str(user["id"]): user for user in client.users()}
        self.names = {
            key: str(
                (user.get("profile") or {}).get("display_name") or user.get("real_name") or "user"
            )
            for key, user in self.users.items()
        }
        self.team_id = str(connection.cursor["team_id"])
        self.kept = int(self.progress.get("kept", 0))
        self.updated = int(self.progress.get("updated", 0))
        self.deleted = int(self.progress.get("deleted", 0))

    def checkpoint(self, **patch: Any) -> None:
        self.progress.update(patch, kept=self.kept, updated=self.updated, deleted=self.deleted)
        self.ctx.jobs.update_progress(
            workspace_id=self.job.workspace_id, job_id=self.job.id, progress=self.progress
        )

    def budget(self) -> None:
        if time.monotonic() >= self.deadline:
            raise ContinueTask(self.progress)
        current = load_connection(self.ctx, self.job, self.connection.id)
        if current.provider != "slack":
            raise PermanentTaskError("unsupported_provider")

    def channels(self) -> list[dict[str, Any]]:
        return mapped_channels(
            self.repo,
            workspace_id=self.job.workspace_id,
            connection_id=self.connection.id,
            channels=list(self.client.channels()),
            users=self.users,
            team_id=self.team_id,
            directory=self.ctx.directory(self.job.workspace_id),
        )

    def ingest(self, message: dict[str, Any], channel: dict[str, Any]) -> None:
        try:
            stored = store_message(
                self.repo,
                workspace_id=self.job.workspace_id,
                connection_id=self.connection.id,
                team_id=self.team_id,
                users=self.users,
                names=self.names,
                message=message,
                channel=channel,
                permalink=self.client.permalink,
            )
        except (ValidationError, ValueError, OverflowError):
            self.progress["invalid"] = int(self.progress.get("invalid", 0)) + 1
            return
        if stored:
            if stored[1]:
                self.kept += 1
            else:
                self.updated += 1

    def thread(
        self,
        parent: dict[str, Any],
        channel: dict[str, Any],
        state: dict[str, Any],
        seen: set[str],
        *,
        force: bool,
    ) -> None:
        ts = str(parent["ts"])
        latest = parent.get("latest_reply")
        threads = state.setdefault("threads", {})
        if not parent.get("thread_ts") and not parent.get("reply_count"):
            return
        if not force and latest is not None and ts in threads and threads[ts] == latest:
            return
        cursor = self.progress.get("reply_cursor") if self.progress.get("thread_ts") == ts else None
        while True:
            self.checkpoint(thread_ts=ts, reply_cursor=cursor)
            self.budget()
            values, following = self.client.replies(
                channel["id"], ts, **({"cursor": cursor} if cursor else {})
            )
            for message in values:
                self.ingest(message, channel)
                seen.add(f"{channel['id']}:{message['ts']}")
            self.checkpoint(seen=sorted(seen))
            if not following:
                break
            if following == cursor:
                raise ProviderError(502, "invalid_cursor")
            cursor = following
        threads[ts] = latest
        self.repo.save_channel(
            workspace_id=self.job.workspace_id,
            connection_id=self.connection.id,
            channel=channel["id"],
            state=state,
        )
        self.checkpoint(thread_ts=None, reply_cursor=None)

    def scan(
        self,
        channel: dict[str, Any],
        state: dict[str, Any],
        start: datetime,
        end: datetime,
        *,
        phase: str,
    ) -> None:
        resume = (
            self.progress.get("channel") == channel["id"] and self.progress.get("phase") == phase
        )
        cursor = self.progress.get("page_token") if resume else None
        seen = set(self.progress.get("seen", [])) if resume else set()
        if not resume:
            self.checkpoint(
                channel=channel["id"],
                phase=phase,
                page_token=None,
                seen=[],
                thread_ts=None,
                reply_cursor=None,
            )
        if phase == "rescan":
            # Replies in this window can belong to parents older than the history window.
            done = set(self.progress.get("rescanned_threads", [])) if resume else set()
            for ts in list(state.get("threads", {})):
                if ts in done:
                    continue
                parent = {"ts": ts, "thread_ts": ts, "latest_reply": state["threads"][ts]}
                try:
                    self.thread(parent, channel, state, seen, force=True)
                except ProviderError as exc:
                    if exc.reason != "thread_not_found":
                        raise
                    state["threads"].pop(ts, None)
                done.add(ts)
                self.checkpoint(rescanned_threads=sorted(done), seen=sorted(seen))
        while True:
            self.checkpoint(page_token=cursor)
            self.budget()
            values, following = self.client.history(
                channel["id"],
                oldest=f"{start.timestamp():.6f}",
                latest=f"{end.timestamp():.6f}",
                inclusive="true",
                **({"cursor": cursor} if cursor else {}),
            )
            for message in values:
                self.ingest(message, channel)
                seen.add(f"{channel['id']}:{message['ts']}")
                self.thread(message, channel, state, seen, force=phase == "rescan")
            self.checkpoint(seen=sorted(seen))
            if not following:
                break
            if following == cursor:
                raise ProviderError(502, "invalid_cursor")
            cursor = following
        if phase == "rescan":
            for external_id in self.repo.existing(
                workspace_id=self.job.workspace_id,
                connection_id=self.connection.id,
                channel=channel["id"],
                start=start,
                end=end,
            ):
                if external_id not in seen:
                    self.deleted += int(
                        self.ctx.records.delete_record(
                            workspace_id=self.job.workspace_id,
                            connection_id=self.connection.id,
                            external_id=external_id,
                        )
                    )
            state["rescanned_at"] = end.date().isoformat()
        self.repo.save_channel(
            workspace_id=self.job.workspace_id,
            connection_id=self.connection.id,
            channel=channel["id"],
            state=state,
        )
        self.checkpoint(page_token=None, seen=[], phase=None, rescanned_threads=[])

    def request_backfill(self, channel: dict[str, Any]) -> None:
        for start, end in month_windows(self.now, self.connection.backfill_months):
            key = f"slack-backfill:{self.connection.id}:{channel['id']}:{start:%Y-%m}"
            payload = {
                "connection_id": str(self.connection.id),
                "channel_id": channel["id"],
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
            }
            job_id = self.ctx.jobs.create(
                workspace_id=self.job.workspace_id,
                job_type="sync.slack",
                idempotency_key=key,
                payload=payload,
                connection_id=self.connection.id,
            )
            if job_id is not None:
                self.ctx.queue.enqueue(
                    TaskEnvelope(
                        workspace_id=self.job.workspace_id,
                        job_id=job_id,
                        idempotency_key=key,
                        type="sync.slack",
                        payload=payload,
                    )
                )

    def run(self) -> dict[str, Any]:
        channels = self.channels()
        resume_channel = self.progress.get("channel")
        for channel in channels:
            if self.payload.channel_id and channel["id"] != self.payload.channel_id:
                continue
            if resume_channel and channel["id"] < resume_channel:
                continue
            if channel["id"] in self.progress.get("completed_channels", []):
                continue
            latest = load_connection(self.ctx, self.job, self.connection.id)
            state = dict((latest.cursor.get("slack_channels") or {}).get(channel["id"], {}))
            if self.payload.window_start is not None and self.payload.window_end is not None:
                self.scan(
                    channel,
                    state,
                    self.payload.window_start,
                    self.payload.window_end,
                    phase="backfill",
                )
            else:
                if not state.get("backfill_requested"):
                    self.request_backfill(channel)
                    state["backfill_requested"] = True
                # Parents outside the incremental window still receive new replies.
                if self.progress.get("phase") not in ("incremental", "rescan"):
                    for ts in list(state.get("threads", {})):
                        self.budget()
                        parents, _ = self.client.history(
                            channel["id"], oldest=ts, latest=ts, inclusive="true"
                        )
                        for parent in parents:
                            self.thread(parent, channel, state, set(), force=False)
                start = datetime.fromisoformat(
                    state.get("latest", (self.now - timedelta(days=7)).isoformat())
                )
                start = min(start, self.now - timedelta(days=7))
                if self.progress.get("phase") != "rescan":
                    self.scan(channel, state, start, self.now, phase="incremental")
                    state["latest"] = self.now.isoformat()
                    self.repo.save_channel(
                        workspace_id=self.job.workspace_id,
                        connection_id=self.connection.id,
                        channel=channel["id"],
                        state=state,
                    )
                if state.get("rescanned_at") != self.now.date().isoformat():
                    self.scan(
                        channel, state, self.now - timedelta(days=7), self.now, phase="rescan"
                    )
            self.checkpoint(
                channel=None,
                phase=None,
                seen=[],
                completed_channels=[*self.progress.get("completed_channels", []), channel["id"]],
            )
        return {
            **self.progress,
            "kept": self.kept,
            "updated": self.updated,
            "deleted": self.deleted,
            "completed_at": datetime.now(UTC).isoformat(),
        }
