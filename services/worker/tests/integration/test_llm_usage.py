"""llm_usage recording and the workspace budget on the local Postgres."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.db import pool as db_pool
from firra.db.llm_usage import LlmUsageRepo
from firra.db.types import WorkspaceId
from tests.conftest import local_settings

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]


@pytest.fixture
def db() -> Iterator[Db]:
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as conn:
        yield conn


@pytest.fixture
def repo() -> Iterator[LlmUsageRepo]:
    pool = db_pool.create_pool(local_settings())
    pool.open()
    try:
        yield LlmUsageRepo(pool)
    finally:
        pool.close()


def _workspace(db: Db, name: str) -> WorkspaceId:
    row = db.execute(
        "insert into public.workspace (name, llm_budget_usd_month) values (%s, 20) returning id",
        (name,),
    ).fetchone()
    assert row is not None
    return WorkspaceId(row["id"])


def test_usage_is_recorded_and_summed_per_workspace(db: Db, repo: LlmUsageRepo) -> None:
    ws, other = _workspace(db, "llm-a"), _workspace(db, "llm-b")
    for workspace, cost in ((ws, "0.4"), (ws, "0.1"), (other, "9")):
        repo.record(
            workspace_id=workspace,
            job_id=None,
            model="gemini-3.5-flash-lite",
            prompt_version="extract.v1",
            input_tokens=1000,
            output_tokens=100,
            cost_usd=Decimal(cost),
        )
    since = datetime.now(UTC) - timedelta(hours=1)
    assert repo.spent_since(workspace_id=ws, since=since) == Decimal("0.5")
    assert repo.spent_since(workspace_id=ws, since=datetime.now(UTC) + timedelta(hours=1)) == 0

    budget = repo.budget(workspace_id=ws)
    assert budget is not None
    assert budget.monthly_usd == Decimal(20)
    assert repo.budget(workspace_id=WorkspaceId(uuid4())) is None
