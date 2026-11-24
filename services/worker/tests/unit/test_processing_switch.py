from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from firra.db.jobs import ClaimedJob
from firra.db.types import JobId, WorkspaceId
from firra.tasks.context import TaskContext
from firra.tasks.errors import PermanentTaskError
from firra.tasks.handlers.process import handle_process_records, request_processing
from firra_contracts import ProcessRecordsPayload
from tests.conftest import local_settings

WS = WorkspaceId(uuid4())


def _ctx(*, enabled: bool) -> TaskContext:
    # No pool: a disabled switch must decide before touching the database or the model.
    return cast(TaskContext, SimpleNamespace(llm=object(), processing_enabled=enabled, pool=None))


def test_processing_is_off_by_default() -> None:
    assert local_settings().processing_enabled is False


def test_disabled_switch_requests_nothing() -> None:
    assert request_processing(_ctx(enabled=False), WS, "sweep") is False


def test_disabled_switch_fails_a_queued_job_without_calls() -> None:
    job = ClaimedJob(WS, JobId(uuid4()), "process.records", attempts=1, max_attempts=5)
    payload = ProcessRecordsPayload.model_validate({"trigger": "sweep"})
    with pytest.raises(PermanentTaskError) as failure:
        handle_process_records(_ctx(enabled=False), job, payload)
    assert failure.value.error_class == "processing_disabled"
