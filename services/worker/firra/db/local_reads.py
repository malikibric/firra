"""SQL of the local read models and the read audit (runs inside ``ReadContext.caller()``)."""

from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from firra.db.types import WorkspaceId, cross_tenant_maintenance


def routines(conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId) -> list[DictRow]:
    return conn.execute(
        """select r.kind::text as kind, r.enabled,
                  (select jsonb_build_object('scheduled_for', rr.scheduled_for,
                                             'status', rr.status::text,
                                             'finished_at', rr.finished_at, 'counts', rr.counts)
                     from public.routine_run rr
                    where rr.workspace_id = %(ws)s and rr.routine_id = r.id
                    order by rr.scheduled_for desc limit 1) as last_run
             from public.routine r where r.workspace_id = %(ws)s""",
        {"ws": workspace_id},
    ).fetchall()


@cross_tenant_maintenance("the owner's own memberships across workspaces, as the owner")
def memberships(conn: psycopg.Connection[DictRow], user_id: UUID) -> list[DictRow]:
    return conn.execute(
        """select m.workspace_id, m.role::text as role, w.name as workspace_name
             from public.membership m join public.workspace w on w.id = m.workspace_id
            where m.user_id = %s""",
        (user_id,),
    ).fetchall()


def write_read_audit(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: WorkspaceId,
    actor_id: UUID,
    action: str,
    target_type: str | None,
    target_id: UUID | None,
    count: int,
) -> None:
    """One audit row per read of client data; a failure raises (the caller returns nothing)."""
    conn.execute(
        """insert into public.audit_log
             (workspace_id, actor_type, actor_id, action, target_type, target_id, metadata)
           values (%s, 'user', %s, %s, %s, %s, %s)""",
        (workspace_id, actor_id, action, target_type, target_id, Jsonb({"count": count})),
    )
