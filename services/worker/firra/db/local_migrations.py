"""Bookkeeping for the local database migrations (not tenant data, so not workspace scoped)."""

import psycopg

from firra.db.types import cross_tenant_maintenance

_TRACKING = """
create schema if not exists firra_local;
create table if not exists firra_local.applied_migration (
  version text primary key,
  applied_at timestamptz not null default now()
);
"""


@cross_tenant_maintenance("the tracking table lists migration versions, not tenant rows")
def applied_versions(conn: psycopg.Connection) -> set[str]:
    conn.execute(_TRACKING)
    return {r[0] for r in conn.execute("select version from firra_local.applied_migration")}


@cross_tenant_maintenance("the tracking table lists migration versions, not tenant rows")
def record_applied(conn: psycopg.Connection, version: str) -> None:
    conn.execute("insert into firra_local.applied_migration (version) values (%s)", (version,))
