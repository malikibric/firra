"""Local database setup: the Supabase shim, then ``supabase/migrations``, once each.

The same SQL files that built the cloud database build the owner's local PostgreSQL, so there is
one schema. Applied versions are recorded in ``firra_local.applied_migration`` (a schema of its
own so the pgTAP RLS-coverage test, which inspects ``public``, is unaffected). Each file runs in
one transaction with its record, so a crash leaves nothing half done.
"""

from pathlib import Path

import psycopg

from firra.db.local_migrations import applied_versions, record_applied
from firra.logging import get_logger
from firra.resources import supabase_dir

log = get_logger(__name__)

SHIM_VERSION = "shim"


def _repo_supabase_dir() -> Path:
    return supabase_dir()


def migrate(
    conninfo: str,
    *,
    migrations_dir: Path | None = None,
    shim: Path | None = None,
) -> list[str]:
    """Apply the shim and every pending migration in order; returns the versions applied now."""
    base = _repo_supabase_dir()
    migrations_dir = migrations_dir or base / "migrations"
    shim = shim or base / "local" / "shim.sql"
    # Numeric order of the version prefix, not the file name: "9_x" must run before "10_x".
    files = sorted(
        migrations_dir.glob("*.sql"), key=lambda f: (int(f.name.split("_", 1)[0]), f.name)
    )
    applied_now: list[str] = []
    with psycopg.connect(conninfo, autocommit=True) as conn:
        done = applied_versions(conn)
        pending: list[tuple[str, Path]] = []
        if SHIM_VERSION not in done:
            pending.append((SHIM_VERSION, shim))
        pending += [
            (f.name.split("_", 1)[0], f) for f in files if f.name.split("_", 1)[0] not in done
        ]
        for version, path in pending:
            with conn.transaction():
                # No parameters: the simple protocol allows many statements in one file.
                conn.execute(path.read_text())
                record_applied(conn, version)
            applied_now.append(version)
            log.info("local.migration.applied", extra={"version": version})
    return applied_now
