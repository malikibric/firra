"""One-time setup statements for the bundled local cluster (not tenant data)."""

import psycopg

from firra.db.types import cross_tenant_maintenance

DATABASE = "firra"


@cross_tenant_maintenance("creating the engine's database in its own private cluster")
def ensure_database(conn: psycopg.Connection) -> None:
    exists = conn.execute("select 1 from pg_database where datname = %s", (DATABASE,)).fetchone()
    if exists is None:
        conn.execute(f"create database {DATABASE}")  # nosemgrep
