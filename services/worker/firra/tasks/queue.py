"""Enqueues follow-up tasks from the worker (continuations of long jobs)."""

import base64
import hashlib
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
import jwt

from firra.crypto.kek import MetadataTokenSource
from firra.db.jobs import PgJobStore
from firra.db.locality import is_local_database
from firra.db.pool import create_pool_for
from firra.db.types import JobId, WorkspaceId
from firra.tasks.auth import LOCAL_AUDIENCE, LOCAL_ISSUER
from firra_contracts import TaskEnvelope

# Shared tasks match apps/api/src/tasks/enqueue.ts; vault.rehydrate enqueue follows in T71.
QUEUE_FOR_TASK = {
    "vault.rehydrate": "sync",
    "diagnostics.ping": "process",
    "sync.backfill_chunk": "sync",
    "sync.incremental": "sync",
    "ingest.upload": "sync",
    "process.records": "extract",
    "signals.evaluate": "process",
    "memory.rebuild": "process",
    "needs.update": "extract",
    "assessment.replay": "process",
    "synth.cards": "synth",
    "brief.meetings": "synth",
    "routines.run": "process",
}


class TaskQueue(Protocol):
    def enqueue(self, envelope: TaskEnvelope, *, delay_seconds: float = 0) -> None: ...


def task_id_for(envelope: TaskEnvelope) -> str:
    return hashlib.sha256(
        f"{envelope.workspace_id}:{envelope.idempotency_key}".encode()
    ).hexdigest()


@dataclass
class CloudTasksQueue:
    project_id: str
    region: str
    worker_url: str
    invoker_email: str
    client: httpx.Client
    tokens: MetadataTokenSource

    def enqueue(self, envelope: TaskEnvelope, *, delay_seconds: float = 0) -> None:
        queue = QUEUE_FOR_TASK[envelope.type]
        parent = f"projects/{self.project_id}/locations/{self.region}/queues/{queue}"
        body = envelope.model_dump_json().encode()
        task: dict[str, Any] = {
            "name": f"{parent}/tasks/{task_id_for(envelope)}",
            "httpRequest": {
                "httpMethod": "POST",
                "url": f"{self.worker_url}/tasks/{envelope.type}",
                "headers": {"Content-Type": "application/json"},
                "body": base64.b64encode(body).decode(),
                "oidcToken": {
                    "serviceAccountEmail": self.invoker_email,
                    "audience": self.worker_url,
                },
            },
        }
        if delay_seconds > 0:
            at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            task["scheduleTime"] = at.isoformat().replace("+00:00", "Z")
        response = self.client.post(
            f"https://cloudtasks.googleapis.com/v2/{parent}/tasks",
            headers={"Authorization": f"Bearer {self.tokens.token()}"},
            json={"task": task},
        )
        if response.status_code == 409:  # ALREADY_EXISTS: named tasks are de-duplicated
            return
        response.raise_for_status()


# A task may run for up to its time budget (settings allow at most 540 s) before it answers. A
# shorter client timeout drops the answer of a task that later ends in a retryable 503, and the job
# then waits queued with nobody to redeliver it; the dispatcher never resends on a timeout.
LOCAL_DELIVERY_TIMEOUT = httpx.Timeout(570.0, connect=10.0)


def local_delivery_client() -> httpx.Client:
    """HTTP client for local task delivery only; provider and metadata calls keep short timeouts."""
    return httpx.Client(timeout=LOCAL_DELIVERY_TIMEOUT)


class LocalDispatchError(Exception):
    """Definitive dispatch failure; contains no response body or request text."""


class LocalDeliveryUncertainError(LocalDispatchError):
    """Transport failed; the worker may still execute this delivery."""


class LocalTaskFailedError(LocalDispatchError):
    """A terminal failed job, including exhausted local delivery attempts."""


@dataclass
class LocalQueue:
    """Local/test dispatcher. Only explicit 503 responses permit redelivery.

    Settings rejects local-secret dispatch outside local/test. Transport failures
    are left for the runner to observe, avoiding duplicate still-running paid calls.
    Direct callers receive LocalDispatchError; background deliveries surface it
    through threading's exception hook.
    """

    worker_url: str
    secret: str
    client: httpx.Client
    sleep: Callable[[float], None] = time.sleep

    def _retry(self, envelope: TaskEnvelope, deliveries: int) -> bool:
        # Read the actual budget, including attempts before this dispatcher started.
        # The eval CLI also dispatches tasks; it needs only its local database
        # URL, not the worker's KMS/provider/auth startup configuration.
        if os.environ.get("FIRRA_ENV", "local") not in ("local", "test"):
            raise LocalDispatchError("local dispatch is only allowed in local/test")
        url = os.environ.get("FIRRA_DATABASE_URL", "")
        if not is_local_database(url):
            raise LocalDispatchError("local dispatch requires a local Postgres URL")
        with create_pool_for(url, min_size=0, max_size=1) as pool:
            decision = PgJobStore(pool).retry_local_delivery(
                workspace_id=WorkspaceId(envelope.workspace_id),
                job_id=JobId(envelope.job_id),
                deliveries=deliveries,
            )
        if decision == "failed":
            raise LocalTaskFailedError("local dispatch retry budget exhausted")
        if decision == "missing":
            raise LocalDispatchError("local dispatch job not found")
        if decision == "unowned":
            raise LocalDeliveryUncertainError("local dispatch job is not queued")
        return decision == "retry"

    def _post(self, envelope: TaskEnvelope) -> None:
        deliveries = 0
        delay = 1.0
        while True:
            now = int(time.time())
            token = jwt.encode(
                {"iss": LOCAL_ISSUER, "aud": LOCAL_AUDIENCE, "iat": now, "exp": now + 60},
                self.secret,
                algorithm="HS256",
            )
            try:
                response = self.client.post(
                    f"{self.worker_url}/tasks/{envelope.type}",
                    content=envelope.model_dump_json(),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
            except httpx.TransportError:
                raise LocalDeliveryUncertainError("local delivery outcome unknown") from None
            deliveries += 1
            if 200 <= response.status_code < 300:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if isinstance(body, dict) and body.get("status") == "failed":
                    raise LocalTaskFailedError("worker acknowledged failed job")
                return
            if response.status_code != 503:
                raise LocalDispatchError(f"local worker returned HTTP {response.status_code}")
            if not self._retry(envelope, deliveries):
                return
            self.sleep(delay)
            delay = min(delay * 2, 30.0)

    def enqueue(self, envelope: TaskEnvelope, *, delay_seconds: float = 0) -> None:
        # Background thread: the current request must finish before the continuation runs.
        timer = threading.Timer(delay_seconds, self._post, args=(envelope,))
        timer.daemon = True
        timer.start()
