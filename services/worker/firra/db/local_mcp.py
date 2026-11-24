"""Workspace-scoped MCP audit, separate from HTTP read audits."""

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId
from firra.local.mcp.request_scope import REQUEST_SCOPES


def audit(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: WorkspaceId,
    actor_id: UUID,
    name: str,
    count: int,
    target_id: UUID | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """insert into public.audit_log
          (workspace_id, actor_type, actor_id, action, target_type, target_id, metadata)
          values (%s, 'mcp', %s, %s, %s, %s, %s)""",
        (
            workspace_id,
            actor_id,
            "mcp." + name,
            "client" if target_id else None,
            target_id,
            Jsonb(
                {
                    "count": count,
                    "via": "local" if REQUEST_SCOPES.get() is None else "link",
                    **(extra or {}),
                }
            ),
        ),
    )


def evidence_context(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: WorkspaceId,
    evidence_id: UUID,
    user_id: UUID,
    pool: ConnectionPool[psycopg.Connection[Any]],
) -> str | None:
    """Owner RLS proves access first; the lead approved this narrow body-read exception.

    Migration 22 intentionally hides bodies from authenticated. Only this opted-in
    context path reads one authorised row with the engine role, rechecking workspace,
    membership and full policy. Decryption and the bounded window stay in the repository.
    """
    from firra.local.bodycrypt import read_body

    authorised = conn.execute(
        """select e.source_record_id from public.evidence e
          join public.client c on c.workspace_id = %(ws)s and c.id = e.client_id
          where e.workspace_id = %(ws)s and e.id = %(id)s and c.policy = 'full'""",
        {"ws": workspace_id, "id": evidence_id},
    ).fetchone()
    if authorised is None:
        return None
    with pool.connection() as privileged:
        row = privileged.execute(
            """select e.quote, r.id, r.body_clean from public.evidence e
              join public.source_record r on r.workspace_id = %(ws)s
                and r.id = e.source_record_id and r.client_id = e.client_id
              join public.client c on c.workspace_id = %(ws)s and c.id = e.client_id
              where e.workspace_id = %(ws)s and e.id = %(id)s
                and r.id = %(record)s and c.policy = 'full'
                and exists(select 1 from public.membership m
                  where m.workspace_id = %(ws)s and m.user_id = %(user)s)
              for share of c""",
            {
                "ws": workspace_id,
                "id": evidence_id,
                "record": authorised["source_record_id"],
                "user": user_id,
            },
        ).fetchone()
        if row is None:
            return None
        body = read_body(pool, workspace_id, row["id"], row["body_clean"])
        if body is None:
            return None
        position = body.find(row["quote"])
        if position < 0:
            return None
        return body[max(0, position - 300) : position + len(row["quote"]) + 300]


def feedback(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: WorkspaceId,
    card_id: UUID,
    verdict: str,
    action: str | None,
    note: str | None,
) -> UUID:
    from firra.db.local_decisions import card_visible

    if not card_visible(conn, workspace_id, card_id):
        raise ValueError("unavailable")
    row = conn.execute(
        "select public.record_decision_feedback(%s,%s,%s,%s) as id",
        (card_id, verdict, action, note),
    ).fetchone()
    if row is None:
        raise ValueError("unavailable")
    return UUID(str(row["id"]))


def outcome(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: WorkspaceId,
    client_id: UUID,
    values: tuple[Any, ...],
) -> UUID:
    from firra.db.local_decisions import client_visible

    if not client_visible(conn, workspace_id, client_id):
        raise ValueError("unavailable")
    row = conn.execute(
        "select public.record_client_outcome(%s,%s,%s,%s,%s,%s,%s,%s) as id", (client_id, *values)
    ).fetchone()
    if row is None:
        raise ValueError("unavailable")
    return UUID(str(row["id"]))


def audit_after_mutation(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: WorkspaceId,
    actor_id: UUID,
    name: str,
    target_id: UUID | None,
) -> None:
    # The RPC and MCP audit must commit together. The caller has already applied RLS;
    # only the audit insert needs the engine role, never another client-data read.
    conn.execute("reset role")
    audit(
        conn, workspace_id=workspace_id, actor_id=actor_id, name=name, count=1, target_id=target_id
    )


def growth(
    conn: psycopg.Connection[Any], *, workspace_id: WorkspaceId, client_id: UUID | None
) -> list[dict[str, Any]]:
    return conn.execute(
        """select n.id as fact_id, n.client_id, n.mention_count, n.thread_count,
            n.first_seen_at, n.last_seen_at, s.id as service_id, s.name as service_name,
            coalesce((select jsonb_agg(l.evidence_id order by l.evidence_id)
              from public.latent_need_evidence l where l.workspace_id = %(ws)s
                and l.latent_need_id = n.id), '[]'::jsonb) as evidence_ids
          from public.latent_need n join public.client c
            on c.workspace_id = %(ws)s and c.id = n.client_id
          join public.workspace_service s on s.workspace_id = %(ws)s
            and s.id = n.workspace_service_id
          where n.workspace_id = %(ws)s and c.policy = 'full' and n.status = 'open'
            and (%(client)s::uuid is null or n.client_id = %(client)s)
          order by n.last_seen_at desc, n.id limit 500""",
        {"ws": workspace_id, "client": client_id},
    ).fetchall()
