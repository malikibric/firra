"""Local task delivery must outwait the longest task, or a late 503 answer is lost."""

import httpx

from firra.app import _queue
from firra.crypto.kek import MetadataTokenSource
from firra.settings import Settings
from firra.tasks.queue import LOCAL_DELIVERY_TIMEOUT, LocalQueue, local_delivery_client
from tests.conftest import local_settings

LONGEST_TASK_SECONDS = 540  # Field limit of task_time_budget_seconds


def test_delivery_read_timeout_exceeds_the_longest_allowed_task_time_budget() -> None:
    assert Settings.model_validate(
        {**local_settings().model_dump(), "task_time_budget_seconds": LONGEST_TASK_SECONDS}
    )
    assert LOCAL_DELIVERY_TIMEOUT.read is not None
    assert LOCAL_DELIVERY_TIMEOUT.read >= LONGEST_TASK_SECONDS + 30
    assert LOCAL_DELIVERY_TIMEOUT.connect == 10.0


def test_the_worker_uses_it_for_local_follow_ups_but_not_for_provider_calls() -> None:
    shared = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
    queue = _queue(local_settings(), shared, MetadataTokenSource(shared))
    assert isinstance(queue, LocalQueue)
    assert queue.client is not shared
    assert queue.client.timeout.read == LOCAL_DELIVERY_TIMEOUT.read
    assert shared.timeout.read == 30.0
    queue.client.close()
    shared.close()


def test_client_factory_returns_independent_clients() -> None:
    first, second = local_delivery_client(), local_delivery_client()
    assert first is not second
    first.close()
    second.close()
