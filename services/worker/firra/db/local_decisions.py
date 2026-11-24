"""SQL of the decisions list, feedback and outcome writes (runs inside ``ReadContext.caller()``)."""

from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

from firra.db.types import WorkspaceId


def decision_rows(
    conn: psycopg.Connection[DictRow],
    *,
    workspace_id: WorkspaceId,
    user_id: UUID,
    status: str | None,
    importance: str | None,
    kind: str | None,
    cursor: tuple[datetime, UUID] | None,
    limit: int,
) -> list[DictRow]:
    """One more row than ``limit`` (the caller pages). RLS hides excluded clients' cards."""
    return conn.execute(
        """select d.id, d.client_id, d.week, d.kind::text as kind, d.importance::text as importance,
                  d.surfaced_on, d.title, d.body, d.play, d.strength::text as strength, d.priority,
                  d.status::text as status, d.created_at, d.facts,
                  c.name as client_name, c.policy::text as client_policy,
                  coalesce((select array_agg(de.evidence_id order by de.evidence_id)
                              from public.decision_evidence de
                             where de.workspace_id = %(ws)s and de.decision_card_id = d.id),
                           '{}') as evidence_ids,
                  (select jsonb_build_object('verdict', f.verdict::text, 'action', f.action::text,
                                             'note', f.note)
                     from public.decision_feedback f
                    where f.workspace_id = %(ws)s and f.decision_card_id = d.id
                      and f.user_id = %(uid)s limit 1) as feedback
             from public.decision_card d
             join public.client c on c.workspace_id = d.workspace_id and c.id = d.client_id
            where d.workspace_id = %(ws)s and c.policy in ('full', 'metadata_only')
              and (%(status)s::text is null or d.status::text = %(status)s)
              and (%(importance)s::text is null or d.importance::text = %(importance)s)
              and (%(kind)s::text is null or d.kind::text = %(kind)s)
              and (%(cursor_at)s::timestamptz is null
                   or d.created_at < %(cursor_at)s
                   or (d.created_at = %(cursor_at)s and d.id < %(cursor_id)s::uuid))
            order by d.created_at desc, d.id desc
            limit %(limit)s""",
        {
            "ws": workspace_id,
            "uid": user_id,
            "status": status,
            "importance": importance,
            "kind": kind,
            "cursor_at": cursor[0] if cursor else None,
            "cursor_id": cursor[1] if cursor else None,
            "limit": limit,
        },
    ).fetchall()


def card_visible(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, card_id: UUID
) -> bool:
    return (
        conn.execute(
            "select 1 as ok from public.decision_card where workspace_id = %s and id = %s",
            (workspace_id, card_id),
        ).fetchone()
        is not None
    )


def client_visible(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, client_id: UUID
) -> bool:
    """An excluded client does not exist for the API (the setup pages still list it)."""
    return (
        conn.execute(
            "select 1 as ok from public.client where workspace_id = %s and id = %s"
            " and policy <> 'excluded'",
            (workspace_id, client_id),
        ).fetchone()
        is not None
    )


def outcome_visible(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, outcome_id: UUID
) -> bool:
    return (
        conn.execute(
            "select 1 as ok from public.outcome where workspace_id = %s and id = %s",
            (workspace_id, outcome_id),
        ).fetchone()
        is not None
    )
