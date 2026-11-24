import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx

from firra.crypto.kek import MetadataTokenSource
from firra.tasks.queue import CloudTasksQueue, task_id_for
from firra_contracts import TaskEnvelope


def envelope() -> TaskEnvelope:
    return TaskEnvelope.model_validate(
        {
            "workspace_id": str(uuid4()),
            "job_id": str(uuid4()),
            "idempotency_key": "sync:1",
            "type": "sync.backfill_chunk",
            "payload": {"connection_id": str(uuid4())},
        }
    )


def test_cloud_tasks_queue_creates_a_named_oidc_task_and_tolerates_duplicates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        requests.append(request)
        return httpx.Response(200 if len(requests) == 1 else 409, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    queue = CloudTasksQueue(
        project_id="p",
        region="europe-west3",
        worker_url="https://worker.run.app",
        invoker_email="inv@p.iam.gserviceaccount.com",
        client=client,
        tokens=MetadataTokenSource(client),
    )
    env = envelope()
    queue.enqueue(env)
    queue.enqueue(env)  # duplicate name -> 409 -> fine

    first = requests[0]
    assert first.url.path == "/v2/projects/p/locations/europe-west3/queues/sync/tasks"
    task = json.loads(first.content)["task"]
    assert task["name"].endswith(f"/tasks/{task_id_for(env)}")
    http = task["httpRequest"]
    assert http["url"] == "https://worker.run.app/tasks/sync.backfill_chunk"
    assert http["oidcToken"] == {
        "serviceAccountEmail": "inv@p.iam.gserviceaccount.com",
        "audience": "https://worker.run.app",
    }
    assert json.loads(base64.b64decode(http["body"]))["job_id"] == str(env.job_id)


def test_cloud_tasks_queue_schedules_delayed_tasks() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        bodies.append(json.loads(request.content)["task"])
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    queue = CloudTasksQueue(
        project_id="p",
        region="europe-west3",
        worker_url="https://worker.run.app",
        invoker_email="inv@p.iam.gserviceaccount.com",
        client=client,
        tokens=MetadataTokenSource(client),
    )
    before = datetime.now(UTC)
    queue.enqueue(envelope())
    queue.enqueue(envelope(), delay_seconds=60)

    assert "scheduleTime" not in bodies[0]
    scheduled = datetime.fromisoformat(bodies[1]["scheduleTime"].replace("Z", "+00:00"))
    assert timedelta(seconds=59) < scheduled - before < timedelta(seconds=62)
