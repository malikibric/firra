"""Synthetic jobs on the private bundle, including loss of the actual runner process."""

import multiprocessing
import os
import tempfile
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.db.jobs import PgJobStore
from firra.db.local_jobs import LocalJobs
from firra.db.pool import create_pool_for
from firra.local.executor import LocalExecutor
from firra.local.pg import PostgresSupervisor
from firra.tasks.registry import REGISTRY, TaskSpec
from firra_contracts import DiagnosticsPingPayload, TaskEnvelope
from tests.unit.local.test_executor import context, until

pytestmark = pytest.mark.integration
PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")


@pytest.fixture
def database() -> Iterator[str]:
    if not PG_BIN:
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    with tempfile.TemporaryDirectory(prefix="firra-executor-", dir="/tmp") as root:
        pg = PostgresSupervisor(Path(PG_BIN), Path(root) / "data", Path(root) / "run")
        try:
            yield pg.ensure_ready()
        finally:
            pg.stop()


def _crash_worker(database: str, started: Any) -> None:
    def block(*args: Any) -> dict[str, Any]:
        started.set()
        multiprocessing.Event().wait()
        return {}

    REGISTRY["diagnostics.ping"] = TaskSpec(DiagnosticsPingPayload, block)
    with create_pool_for(database) as pool:
        executor = LocalExecutor(replace(context(PgJobStore(pool)), pool=pool))
        executor.start()
        multiprocessing.Event().wait()


def test_ping_idempotency_and_crash_recovery(database: str) -> None:
    with psycopg.connect(database, row_factory=dict_row, autocommit=True) as conn:
        ws = conn.execute(
            "insert into public.workspace (name) values ('executor fixture') returning id"
        ).fetchone()
        assert ws is not None
        workspace_id = ws["id"]
        job_id = uuid4()
        env = TaskEnvelope(
            workspace_id=workspace_id,
            job_id=job_id,
            idempotency_key="ping",
            type="diagnostics.ping",
            payload={"requested_at": "2026-10-05T00:00:00Z"},
        )
        with create_pool_for(database) as pool:
            jobs = LocalJobs(pool)
            assert jobs.ensure(env).job_id == job_id
            assert jobs.ensure(env.model_copy(update={"job_id": uuid4()})).job_id == job_id
            spawn = multiprocessing.get_context("spawn")
            started = spawn.Event()
            process = spawn.Process(target=_crash_worker, args=(database, started))
            process.start()
            try:
                assert started.wait(10)
                running = conn.execute(
                    "select status, attempts from public.job where id=%s", (job_id,)
                ).fetchone()
                assert running == {"status": "running", "attempts": 1}
            finally:
                process.kill()
                process.join(10)
            assert not process.is_alive()
            # Non-resumable rows can be either queued or running at restart.
            for status in ("queued", "running"):
                conn.execute(
                    "insert into public.job (workspace_id,type,idempotency_key,payload,status) "
                    "values (%s,'diagnostics.llm_probe',%s,'{}',%s)",
                    (workspace_id, f"probe-{status}", status),
                )
            executor = LocalExecutor(replace(context(PgJobStore(pool)), pool=pool))
            executor.start()
            try:

                def finished() -> bool:
                    row = conn.execute(
                        "select status from public.job where id=%s", (job_id,)
                    ).fetchone()
                    return row is not None and row["status"] == "succeeded"

                until(finished)
                executor.enqueue(env)
                until(lambda: not executor._pending and not executor._active)
            finally:
                executor.stop()
            row = conn.execute(
                "select attempts, progress from public.job where id=%s", (job_id,)
            ).fetchone()
            assert row is not None and row["attempts"] == 2 and "pong_at" in row["progress"]
            probes = conn.execute(
                "select status,error_class from public.job where type='diagnostics.llm_probe'"
            ).fetchall()
            assert probes == [{"status": "failed", "error_class": "abandoned_on_restart"}] * 2


def test_jobs_that_failed_for_missing_setup_run_again_at_start(database: str) -> None:
    with psycopg.connect(database, row_factory=dict_row, autocommit=True) as conn:
        ws = conn.execute(
            "insert into public.workspace (name) values ('setup retry') returning id"
        ).fetchone()
        assert ws is not None
        for key, error in (("setup", "google_not_configured"), ("real", "token_revoked")):
            conn.execute(
                "insert into public.job (workspace_id,type,idempotency_key,payload,status,"
                "error_class,attempts) values (%s,'diagnostics.ping',%s,%s,'failed',%s,3)",
                (ws["id"], key, '{"requested_at": "2026-10-05T00:00:00Z"}', error),
            )
        with create_pool_for(database) as pool:
            recovered = LocalJobs(pool).recover(frozenset())
        assert [e.idempotency_key for e in recovered] == ["setup"]
        rows = {
            r["idempotency_key"]: (r["status"], r["error_class"], r["attempts"])
            for r in conn.execute(
                "select idempotency_key, status, error_class, attempts from public.job"
            )
        }
        assert rows["setup"] == ("queued", None, 0)
        assert rows["real"] == ("failed", "token_revoked", 3)


def test_only_the_newest_suggestion_scan_survives_a_restart(database: str) -> None:
    with psycopg.connect(database, row_factory=dict_row, autocommit=True) as conn:
        ws = conn.execute(
            "insert into public.workspace (name) values ('suggest restart') returning id"
        ).fetchone()
        assert ws is not None
        for minute, key in ((1, "old"), (2, "older-running"), (3, "newest")):
            conn.execute(
                "insert into public.job (workspace_id,type,idempotency_key,payload,status,"
                "created_at) values (%s,'onboarding.suggest',%s,'{}',%s,"
                "now() - make_interval(mins => %s))",
                (ws["id"], key, "running" if key == "older-running" else "queued", 10 - minute),
            )
        with create_pool_for(database) as pool:
            recovered = LocalJobs(pool).recover(frozenset())
        assert [e.idempotency_key for e in recovered] == ["newest"]
