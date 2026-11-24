"""Lifecycle CLI for disposable bundled PostgreSQL tests."""

import contextlib
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

import psycopg
from build import install

from firra.local.pg import PostgresSupervisor


def _enable_pgtap(conninfo: str) -> None:
    """Supabase creates pgTAP in schema `extensions` before the migrations run and lets every role
    call it (the migrations revoke the default EXECUTE grant for functions created afterwards, so
    grant it here)."""
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("create extension if not exists pgtap with schema extensions")
        conn.execute("grant execute on all functions in schema extensions to public")
        conn.execute("grant select on all tables in schema extensions to public")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "start":
        binary = Path(os.environ.get("FIRRA_LOCAL_PG_BIN", "~/firra-tools/engine/postgres/bin"))
        binary = binary.expanduser().resolve()
        root = Path(tempfile.mkdtemp(prefix="firra-test-db-", dir="/tmp"))
        pg = PostgresSupervisor(root / "postgres/bin", root / "data", root / "run")
        try:
            shutil.copytree(binary.parent, root / "postgres")
            install(root / "postgres/share/extension")
            with contextlib.redirect_stdout(sys.stderr):
                conninfo = pg.ensure_ready()
            _enable_pgtap(conninfo)
        except BaseException:
            pg.stop()
            shutil.rmtree(root)
            raise
        for key, value in {
            "FIRRA_LOCAL_DB_ROOT": str(root),
            "FIRRA_LOCAL_PG_BIN": str(pg.bin_dir),
            "FIRRA_DATABASE_URL": conninfo,
        }.items():
            print(f"export {key}={shlex.quote(value)}")  # noqa: T201 — requested env output
    elif action == "stop":
        root = Path(os.environ["FIRRA_LOCAL_DB_ROOT"])
        if (
            root.is_symlink()
            or root.parent != Path("/tmp")  # noqa: S108 — validates mkdtemp root
            or not root.name.startswith("firra-test-db-")
            or root.stat().st_uid != os.getuid()
        ):
            raise RuntimeError("invalid disposable cluster directory")
        pg = PostgresSupervisor(root / "postgres/bin", root / "data", root / "run")
        pg.stop()
        shutil.rmtree(root)
    else:
        raise SystemExit("usage: scripts/local-db.sh start|stop")


if __name__ == "__main__":
    main()
