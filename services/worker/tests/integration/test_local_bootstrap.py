"""One local owner and workspace on the bundled database; RLS works for that owner (plan L1-03)."""

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from firra.local.bootstrap import ensure_local_owner
from firra.local.pg import PostgresSupervisor

pytestmark = pytest.mark.integration
PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")


@pytest.fixture
def conninfo() -> Iterator[str]:
    if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    root = Path(tempfile.mkdtemp(prefix="firra-boot-", dir="/tmp"))
    pg = PostgresSupervisor(Path(PG_BIN), root / "data", root / "run")
    try:
        yield pg.ensure_ready()
    finally:
        pg.stop()
        shutil.rmtree(root, ignore_errors=True)


def test_owner_and_workspace_are_created_once_and_rls_applies(conninfo: str) -> None:
    first = ensure_local_owner(conninfo, timezone="Europe/Sarajevo")
    assert ensure_local_owner(conninfo, timezone="UTC") == first  # idempotent, first time zone kept
    with psycopg.connect(conninfo) as conn:
        assert conn.execute("select count(*) from auth.users").fetchone() == (1,)
        row = conn.execute(
            "select timezone, name from public.workspace where id = %s", (first.workspace_id,)
        ).fetchone()
        assert row == ("Europe/Sarajevo", "My firm")
        # As the signed-in owner, RLS shows the workspace; without a caller it shows nothing.
        conn.execute("set local role authenticated")
        assert conn.execute("select count(*) from public.workspace").fetchone() == (0,)
        conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(first.user_id),))
        assert conn.execute("select count(*) from public.workspace").fetchone() == (1,)
        conn.rollback()
