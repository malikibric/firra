"""Independent oracle for local migrations (L1-08, requirements 10-11).

Written from ``firra/local/db.py``'s contract (shim once, then migrations in
order, each file in one transaction with its version row) and the
``00_rls_coverage`` rule (RLS + ``workspace_id not null`` on every public
table). Needs the bundled binaries; skipped cleanly without them.
"""

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from firra.local.db import migrate

pytestmark = pytest.mark.integration

PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")


def _bin() -> None:
    if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")


def _blank_conninfo(root: Path) -> str:
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "en_US.UTF-8"}
    subprocess.run(  # noqa: S603
        [
            f"{PG_BIN}/initdb",
            "-D",
            str(root / "data"),
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
    subprocess.run(  # noqa: S603
        [
            f"{PG_BIN}/pg_ctl",
            "-D",
            str(root / "data"),
            "-o",
            f"-c listen_addresses='' -c unix_socket_directories={root}",
            "-w",
            "-l",
            str(root / "log"),
            "start",
        ],
        check=True,
        capture_output=True,
        env=env,
    )
    return f"host={root} user=postgres dbname=postgres"


@pytest.fixture
def conninfo() -> Iterator[str]:
    _bin()
    root = Path(tempfile.mkdtemp(prefix="firra-oracle-mig-", dir="/tmp"))
    info = _blank_conninfo(root)
    yield info
    subprocess.run(  # noqa: S603
        [f"{PG_BIN}/pg_ctl", "-D", str(root / "data"), "-m", "fast", "-w", "stop"],
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "en_US.UTF-8"},
    )
    shutil.rmtree(root, ignore_errors=True)


def _versions(conn: psycopg.Connection[Any]) -> set[str]:
    return {r[0] for r in conn.execute("select version from firra_local.applied_migration")}


def test_10_migrate_twice_is_a_noop(conninfo: str) -> None:
    """Spec: migrate() twice; the second run applies nothing."""
    assert migrate(conninfo) != []
    assert migrate(conninfo) == []


def test_10_failed_file_leaves_no_partial_objects_or_version(conninfo: str, tmp_path: Path) -> None:
    """Spec: a file failing halfway leaves no objects and no version row; fixed, it applies."""
    mig = tmp_path / "migrations"
    mig.mkdir()
    bad = mig / "9000_oracle_bad.sql"
    bad.write_text("create table oracle_half(id int);\nthis is not sql;\n")
    (tmp_path / "shim.sql").write_text("select 1;")
    with pytest.raises(Exception, match="syntax error"):  # any error aborts the file
        migrate(conninfo, migrations_dir=mig, shim=tmp_path / "shim.sql")
    with psycopg.connect(conninfo) as check:
        assert check.execute("select to_regclass('public.oracle_half')").fetchone() == (None,)
        assert "9000" not in _versions(check)
    bad.write_text("create table oracle_half(id int);\n")
    applied = migrate(conninfo, migrations_dir=mig, shim=tmp_path / "shim.sql")
    assert "9000" in applied
    with psycopg.connect(conninfo) as check:
        assert check.execute("select to_regclass('public.oracle_half')").fetchone() == (
            "oracle_half",
        )


def test_10_order_is_by_version_not_filename(conninfo: str, tmp_path: Path) -> None:
    """Spec: version 9 applies before version 10 though '10_…' sorts first lexically."""
    mig = tmp_path / "migrations"
    mig.mkdir()
    (tmp_path / "shim.sql").write_text("select 1;")
    (mig / "9_oracle_base.sql").write_text("create table oracle_base(id int);\n")
    (mig / "10_oracle_view.sql").write_text("create view oracle_v as select id from oracle_base;\n")
    try:
        applied = migrate(conninfo, migrations_dir=mig, shim=tmp_path / "shim.sql")
    except Exception as exc:  # lexical order would apply 10 before 9
        pytest.fail(
            f"migrations applied in lexical filename order, not by version: {exc}", pytrace=False
        )
    assert applied.index("9") < applied.index("10")
    with psycopg.connect(conninfo) as check:
        assert check.execute("select to_regclass('public.oracle_v')").fetchone() == ("oracle_v",)


def _seed_tenant(conninfo: str) -> None:
    with psycopg.connect(conninfo, autocommit=True) as conn:
        workspace = conn.execute(
            "insert into public.workspace (name) values ('oracle') returning id"
        ).fetchone()
        assert workspace is not None
        conn.execute(
            "insert into public.client (workspace_id, name) values (%s, 'oracle')",
            (workspace[0],),
        )


def test_11_rls_enabled_and_workspace_id_not_null(conninfo: str) -> None:
    """Spec: mirrors 00_rls_coverage — RLS on every public table, workspace_id not null."""
    migrate(conninfo)
    with psycopg.connect(conninfo) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "select relname from pg_class "
                "where relnamespace = 'public'::regnamespace and relkind = 'r'"
            )
        ]
        assert tables, "expected migrated public tables"
        unprotected = [
            t
            for t in tables
            if conn.execute(
                "select not relrowsecurity from pg_class where relname = %s", (t,)
            ).fetchone()
            == (True,)
        ]
        assert unprotected == [], f"tables without RLS: {unprotected}"
        nullable = [
            r[0]
            for r in conn.execute(
                "select table_name from information_schema.columns "
                "where table_schema = 'public' and column_name = 'workspace_id' "
                "and is_nullable = 'YES'"
            )
        ]
        assert nullable == [], f"nullable workspace_id: {nullable}"


def test_11_anon_has_no_table_access(conninfo: str) -> None:
    """Spec: anon has no table grants."""
    migrate(conninfo)
    _seed_tenant(conninfo)
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("set role anon")
        with pytest.raises(psycopg.Error):
            conn.execute("select count(*) from public.client").fetchone()


def test_11_authenticated_without_claim_sees_nothing(conninfo: str) -> None:
    """Spec: authenticated without request.jwt.claim.sub sees no tenant rows."""
    migrate(conninfo)
    _seed_tenant(conninfo)
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("set role authenticated")
        row = conn.execute("select count(*) from public.client").fetchone()
        assert row is not None and row[0] == 0


def test_11_service_role_reads_tenant_rows(conninfo: str) -> None:
    """Spec: service_role bypasses RLS and the shim grants it full table access
    (Supabase-cloud parity for the engine's privileged paths)."""
    migrate(conninfo)
    _seed_tenant(conninfo)
    with psycopg.connect(conninfo, autocommit=True) as conn:
        row = conn.execute(
            "select rolbypassrls from pg_roles where rolname = 'service_role'"
        ).fetchone()
        assert row is not None and row[0] is True
        conn.execute("set role service_role")
        row = conn.execute("select count(*) from public.client").fetchone()
        assert row is not None and row[0] == 1


def test_11_auth_uid_follows_claim_within_a_transaction(conninfo: str) -> None:
    """Spec: auth.uid() follows request.jwt.claim.sub within a transaction only."""
    migrate(conninfo)
    uid = "22222222-2222-2222-2222-222222222222"
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (uid,))
        row = conn.execute("select auth.uid()::text").fetchone()
        assert row == (None,), "claim leaked across transactions"
    with psycopg.connect(conninfo) as conn, conn.transaction():
        conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (uid,))
        row = conn.execute("select auth.uid()::text").fetchone()
        assert row == (uid,)
