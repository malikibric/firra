"""Local sweeps deduplicate jobs; one post-wake routine scans since the last success."""

import os
import tempfile
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.db.jobs import PgJobStore
from firra.db.pool import create_pool_for
from firra.db.routine_changes import RoutineChangesRepo
from firra.db.routines import RoutinesRepo
from firra.db.types import WorkspaceId
from firra.local.pg import PostgresSupervisor
from firra.maintenance.sweep import run_sweep
from tests.integration.test_sweep import CapturingQueue
from tests.unit.local.test_executor import context

pytestmark = pytest.mark.integration
PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")


def test_local_slots_next_day_and_three_day_gap() -> None:
    if not PG_BIN:
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    with tempfile.TemporaryDirectory(prefix="firra-scheduler-", dir="/tmp") as root:
        pg = PostgresSupervisor(Path(PG_BIN), Path(root) / "data", Path(root) / "run")
        try:
            database = pg.ensure_ready()
            with psycopg.connect(database, row_factory=dict_row, autocommit=True) as conn:
                row = conn.execute(
                    "insert into public.workspace (name, timezone) "
                    "values ('scheduler fixture','UTC') returning id"
                ).fetchone()
                assert row is not None
                ws = WorkspaceId(row["id"])
                conn.execute(
                    "insert into public.connection (workspace_id,provider,mode,status,"
                    "account_label,"
                    "external_account_id,token_ciphertext) "
                    "values (%s,'gmail','user','active','fixture','fixture','v1.x.y')",
                    (ws,),
                )
                with create_pool_for(database) as pool:
                    queue = CapturingQueue()
                    ctx = replace(context(PgJobStore(pool)), pool=pool, queue=queue)
                    repo = RoutinesRepo(pool)
                    for day in (5, 6, 9):
                        now = datetime(2026, 10, day, 9, tzinfo=UTC)
                        first = run_sweep(ctx, now)
                        assert first["enqueued"] == 1 and first["routines"] == 2
                        assert run_sweep(ctx, now)["enqueued"] == 0
                        assert run_sweep(ctx, now)["routines"] == 0
                        due = repo.due_routines(now=now)
                        assert {item.local_date for item in due} == {now.date()}
                        for item in due:
                            if day == 9:
                                assert RoutineChangesRepo(pool).since(
                                    workspace_id=ws, as_of=now.date()
                                ) == date(2026, 10, 6)
                            run = repo.claim_run(
                                workspace_id=ws,
                                routine_id=item.routine_id,
                                scheduled_for=item.local_date,
                            )
                            assert run is not None
                            repo.finish_run(
                                workspace_id=ws, run_id=run["id"], status="succeeded", counts={}
                            )
                    tasks = [env for env in queue.envelopes if env.type == "routines.run"]
                    assert len(tasks) == 6
                    assert {env.payload["scheduled_for"] for env in tasks} == {
                        "2026-10-05",
                        "2026-10-06",
                        "2026-10-09",
                    }
        finally:
            pg.stop()
