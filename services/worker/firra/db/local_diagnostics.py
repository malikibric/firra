"""Content-free counts for the diagnostics export: statuses, kinds and error classes only."""

from typing import Any

import psycopg
from psycopg.rows import DictRow

from firra.db.types import WorkspaceId, cross_tenant_maintenance


@cross_tenant_maintenance("counts applied local migrations; a schema fact, not tenant data")
def migration_count(conn: psycopg.Connection[DictRow]) -> int:
    row = conn.execute("select count(*)::int as n from firra_local.applied_migration").fetchone()
    return 0 if row is None else int(row["n"])


def collect(conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId) -> dict[str, Any]:
    ws = {"ws": workspace_id}
    return {
        "migrations": migration_count(conn),
        "clients": conn.execute(
            "select status::text as status, policy::text as policy, count(*)::int as count"
            " from public.client where workspace_id = %(ws)s group by 1, 2 order by 1, 2",
            ws,
        ).fetchall(),
        "records": conn.execute(
            "select provider::text as provider, kind::text as kind,"
            " processing_state::text as processing_state, count(*)::int as count"
            " from public.source_record where workspace_id = %(ws)s"
            " group by 1, 2, 3 order by 1, 2, 3",
            ws,
        ).fetchall(),
        "jobs": conn.execute(
            "select type, status::text as status, error_class, count(*)::int as count"
            " from public.job where workspace_id = %(ws)s group by 1, 2, 3 order by 1, 2, 3",
            ws,
        ).fetchall(),
        "connections": conn.execute(
            "select provider::text as provider, status::text as status, last_error_class,"
            " last_synced_at from public.connection where workspace_id = %(ws)s"
            " order by provider, created_at",
            ws,
        ).fetchall(),
    }
