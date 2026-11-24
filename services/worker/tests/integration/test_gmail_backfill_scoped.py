"""Backfill lists only mail that names a mapped client, and reads nothing before any is mapped."""

from typing import Any

import psycopg
import pytest

from firra.db.types import WorkspaceId
from tests.fake_google import FakeGoogle
from tests.integration.test_sync_catchup_oracle import (  # noqa: F401 - the module's world fixture
    _backfill_september,
    world,
)

pytestmark = pytest.mark.integration


def test_backfill_query_is_scoped_to_client_domains(world: dict[str, Any]) -> None:  # noqa: F811
    fake: FakeGoogle = world["fake"]
    fake.list_queries.clear()
    _backfill_september(world, "gmail")
    assert fake.list_queries, "a mapped client means the window is listed"
    query = fake.list_queries[0]
    assert "from:acme.com" in query and "to:acme.com" in query and "cc:acme.com" in query


def test_nothing_is_listed_before_a_client_is_mapped(world: dict[str, Any]) -> None:  # noqa: F811
    fake: FakeGoogle = world["fake"]
    ws = WorkspaceId(world["ws"])
    with psycopg.connect(world["conninfo"], autocommit=True) as db:
        saved = db.execute(
            "select client_id, domain from public.client_domain where workspace_id = %s", (ws,)
        ).fetchall()
        db.execute("delete from public.client_domain where workspace_id = %s", (ws,))
    try:
        fake.list_queries.clear()
        _backfill_september(world, "gmail")
        assert fake.list_queries == []
    finally:
        with psycopg.connect(world["conninfo"], autocommit=True) as db:
            for client_id, domain in saved:
                db.execute(
                    "insert into public.client_domain (workspace_id, client_id, domain)"
                    " values (%s,%s,%s)",
                    (ws, client_id, domain),
                )
