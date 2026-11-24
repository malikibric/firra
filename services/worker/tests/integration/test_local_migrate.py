"""The shim plus the unchanged migrations build a working schema on the bundled Postgres (L1-01).

Needs the bundled Postgres binaries (``FIRRA_LOCAL_PG_BIN``: the directory with initdb,
pg_ctl, postgres); skipped otherwise. It starts its own throwaway cluster on a unix socket,
never the Supabase CLI database.
"""

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from firra.local.db import migrate

pytestmark = pytest.mark.integration

PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")


@pytest.fixture
def conninfo() -> Iterator[str]:
    if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    root = tempfile.mkdtemp(prefix="firra-pg-", dir="/tmp")  # unix socket paths are short
    data = f"{root}/data"
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "en_US.UTF-8"}
    subprocess.run(  # noqa: S603
        [
            f"{PG_BIN}/initdb",
            "-D",
            data,
            "-U",
            "postgres",
            "-A",
            "trust",
            "-E",
            "UTF8",
            "--locale=en_US.UTF-8",
        ],
        check=True,
        capture_output=True,
        env=env,
    )
    options = f"-c listen_addresses='' -c unix_socket_directories={root}"
    subprocess.run(  # noqa: S603
        [f"{PG_BIN}/pg_ctl", "-D", data, "-o", options, "-w", "-l", f"{root}/log", "start"],
        check=True,
        capture_output=True,
        env=env,
    )
    try:
        yield f"host={root} user=postgres dbname=postgres"
    finally:
        subprocess.run(  # noqa: S603
            [f"{PG_BIN}/pg_ctl", "-D", data, "-m", "fast", "-w", "stop"],
            capture_output=True,
            env=env,
        )
        shutil.rmtree(root, ignore_errors=True)


def test_shim_and_migrations_apply_once(conninfo: str) -> None:
    first = migrate(conninfo)
    assert first[0] == "shim"
    migrations = list((Path(__file__).parents[4] / "supabase" / "migrations").glob("*.sql"))
    assert len(first) == 1 + len(migrations)
    assert migrate(conninfo) == []  # re-running is harmless
    with psycopg.connect(conninfo) as conn:
        assert conn.execute("select to_regclass('public.client')").fetchone() == ("client",)
        assert conn.execute("select length(extensions.gen_random_bytes(16))").fetchone() == (16,)
        roles = {
            r[0]
            for r in conn.execute(
                "select rolname from pg_roles "
                "where rolname in ('anon','authenticated','service_role')"
            )
        }
        assert roles == {"anon", "authenticated", "service_role"}
        # the caller id comes from the transaction's JWT claim, as in Supabase
        uid = "11111111-1111-1111-1111-111111111111"
        conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (uid,))
        row = conn.execute("select auth.uid()::text").fetchone()
        assert row == (uid,)
