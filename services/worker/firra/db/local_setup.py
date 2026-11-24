"""SQL of the setup writes and the two setup reads (runs inside ``ReadContext.caller()``)."""

from uuid import UUID

import psycopg
from psycopg.rows import DictRow

from firra.db.types import WorkspaceId

_VISIBLE = {
    "client": "select 1 as ok from public.client where workspace_id = %s and id = %s",
    "workspace_service": (
        "select 1 as ok from public.workspace_service where workspace_id = %s and id = %s"
    ),
    "connection": "select 1 as ok from public.connection where workspace_id = %s and id = %s",
}


def target_visible(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, table: str, target_id: UUID
) -> bool:
    """The URL's workspace must own the target (RPCs that take only an id do not check it)."""
    return conn.execute(_VISIBLE[table], (workspace_id, target_id)).fetchone() is not None


def services(conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId) -> list[DictRow]:
    return conn.execute(
        """select id, name, description, keywords, created_at
             from public.workspace_service where workspace_id = %s order by name""",
        (workspace_id,),
    ).fetchall()


def domains(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, client_id: UUID
) -> list[str]:
    rows = conn.execute(
        "select domain from public.client_domain where workspace_id = %s and client_id = %s"
        " order by domain",
        (workspace_id, client_id),
    ).fetchall()
    return [r["domain"] for r in rows]
