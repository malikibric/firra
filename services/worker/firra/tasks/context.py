"""Dependencies handed to task handlers (built once in the app factory)."""

from dataclasses import dataclass
from pathlib import Path

import httpx
from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.connectors.google.auth import GoogleAuth
from firra.connectors.microsoft.auth import MicrosoftAuth
from firra.connectors.slack.local_tokens import LocalSlackTokens
from firra.crypto.keyring import Keyring
from firra.db.connections import ConnectionRepo
from firra.db.directory import load_directory
from firra.db.jobs import JobStore
from firra.db.records import RecordRepo
from firra.db.types import WorkspaceId
from firra.llm.service import Llm
from firra.policy.minimize import Directory
from firra.storage.raw import RawStore
from firra.tasks.queue import TaskQueue


@dataclass(frozen=True)
class TaskContext:
    pool: ConnectionPool[Connection[DictRow]]
    jobs: JobStore
    connections: ConnectionRepo
    records: RecordRepo
    raw: RawStore
    keyring: Keyring
    queue: TaskQueue
    http: httpx.Client
    google: GoogleAuth
    microsoft: MicrosoftAuth
    time_budget_seconds: float
    # Gemini on Vertex AI; None where no Vertex location is configured.
    llm: Llm | None = None
    # process.records (paid model calls) runs only when switched on (FIRRA_PROCESSING_ENABLED).
    processing_enabled: bool = False
    # Push notifications (optional; the 15-minute sweep works without them).
    gmail_push_topic: str | None = None
    microsoft_notification_url: str | None = None
    # Available only in the local engine; Slack tokens never use the cloud keyring.
    slack: LocalSlackTokens | None = None
    # The engine home (owner settings such as the retention period live there).
    home: Path | None = None

    def directory(self, workspace_id: WorkspaceId) -> Directory:
        return load_directory(self.pool, workspace_id=workspace_id)
