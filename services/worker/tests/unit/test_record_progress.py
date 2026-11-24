"""Record completion and progress share a lazy transaction; one counter update per record."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from firra.db.jobs import ClaimedJob
from firra.db.processing import ProcessingRepo
from firra.db.types import JobId, WorkspaceId


def setup_repo() -> tuple[ProcessingRepo, MagicMock, MagicMock, ClaimedJob]:
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    job = ClaimedJob(WorkspaceId(uuid4()), JobId(uuid4()), "process.records", 1, 3)
    conn.execute.return_value.fetchone.return_value = {"id": job.id}
    return ProcessingRepo(pool), pool, conn, job


def test_acquires_connection_only_after_model_work_and_updates_once_per_record() -> None:
    repo, pool, conn, job = setup_repo()
    for completed in range(1, 648):
        before = pool.connection.call_count
        snapshot = {"extracted": completed}
        with repo.record_progress(job=job, progress=snapshot.copy):
            assert pool.connection.call_count == before
            repo.mark(workspace_id=job.workspace_id, record_id=uuid4(), state="done")
    updates = [c for c in conn.execute.call_args_list if "update public.job" in c.args[0]]
    assert len(updates) == 647
    assert pool.connection.call_count == 647
    params = updates[-1].args[1]
    assert params["progress"].obj == {"extracted": 647}
    assert params["workspace_id"] == job.workspace_id and params["job_id"] == job.id
    assert params["attempt"] == 1


def test_retryable_error_before_completion_writes_no_progress() -> None:
    repo, pool, conn, job = setup_repo()
    with (
        pytest.raises(RuntimeError, match="provider failed"),
        repo.record_progress(job=job, progress=lambda: {"extracted": 1}),
    ):
        raise RuntimeError("provider failed")
    pool.connection.assert_not_called()
    conn.execute.assert_not_called()
    # The same repo remains usable for the next record.
    with repo.record_progress(job=job, progress=lambda: {"extracted": 1}):
        repo.mark(workspace_id=job.workspace_id, record_id=uuid4(), state="done")


def test_lost_job_ownership_raises_inside_transaction() -> None:
    repo, _, conn, job = setup_repo()
    conn.execute.return_value.fetchone.return_value = None
    with (
        pytest.raises(RuntimeError, match="ownership lost"),
        repo.record_progress(job=job, progress=lambda: {"extracted": 1}),
    ):
        repo.mark(workspace_id=job.workspace_id, record_id=uuid4(), state="done")
    # Outer transaction sees the error, rather than committing a done record.
    assert any(
        c.args[0] is RuntimeError for c in conn.transaction.return_value.__exit__.call_args_list
    )
