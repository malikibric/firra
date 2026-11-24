"""Workspace-scoped evidence, timeline and replay reads under caller RLS."""

from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

from firra.db.types import WorkspaceId
from firra.local.paging import PageQuery


def visible(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, client_id: UUID
) -> DictRow | None:
    return conn.execute(
        """select id, policy::text from public.client
        where workspace_id = %s and id = %s and policy in ('full', 'metadata_only')""",
        (workspace_id, client_id),
    ).fetchone()


def timeline(
    conn: psycopg.Connection[DictRow],
    workspace_id: WorkspaceId,
    client_id: UUID,
    query: PageQuery,
    now: datetime,
    *,
    metadata_only: bool,
) -> list[DictRow]:
    cursor = query.cursor
    rows = conn.execute(
        """with events as (
          select 'state:' || a.id::text as id, a.as_of::timestamp at time zone 'UTC' as occurred_at,
                 jsonb_build_object('kind', 'state_change', 'from', a.previous_risk_state,
                                    'to', a.risk_state) as fields
            from public.account_assessment a
           where a.workspace_id = %(ws)s and a.client_id = %(client)s and a.run_kind = 'live'
             and a.previous_risk_state is not null and a.previous_risk_state <> a.risk_state
          union all
          select 'signal:' || e.id::text, e.as_of,
                 jsonb_build_object('kind', 'signal', 'type', s.type, 'change', e.change,
                   'severity', e.severity, 'evidence_ids', case when s.basis = 'metadata'
                    then '[]'::jsonb else coalesce((select jsonb_agg(l.evidence_id)
                      from public.signal_evidence l
                     where l.workspace_id = %(ws)s and l.signal_id = s.id), '[]'::jsonb) end)
            from public.signal_event e join public.signal s
              on s.workspace_id = %(ws)s and s.id = e.signal_id
           where e.workspace_id = %(ws)s and e.client_id = %(client)s
             and (not %(meta)s or s.basis = 'metadata')
          union all
          select 'outcome:' || o.id::text, o.occurred_at::timestamp at time zone 'UTC',
                 jsonb_build_object('kind', 'outcome', 'event', o.event)
            from public.outcome o where o.workspace_id = %(ws)s and o.client_id = %(client)s
          union all
          select 'commitment_created:' || c.id::text, c.stated_at,
                 jsonb_build_object('kind', 'commitment', 'change', 'created',
                                    'owner_side', c.owner_side, 'status', c.status)
            from public.commitment c where c.workspace_id = %(ws)s and c.client_id = %(client)s
              and not %(meta)s
          union all
          select 'commitment_due:' || c.id::text, c.due_date::timestamp at time zone 'UTC',
                 jsonb_build_object('kind', 'commitment', 'change', 'due',
                                    'owner_side', c.owner_side, 'status', c.status)
            from public.commitment c where c.workspace_id = %(ws)s and c.client_id = %(client)s
              and c.due_date is not null and not %(meta)s
          union all
          select 'commitment_closed:' || c.id::text, c.closed_at,
                 jsonb_build_object('kind', 'commitment', 'change', 'closed',
                                    'owner_side', c.owner_side, 'status', c.status)
            from public.commitment c where c.workspace_id = %(ws)s and c.client_id = %(client)s
              and c.closed_at is not null and not %(meta)s
        ) select id, occurred_at, fields from events
           where occurred_at <= %(upper)s::timestamptz and
                 (%(cursor_id)s::text is null or occurred_at < %(upper)s::timestamptz
                  or id < %(cursor_id)s)
           order by occurred_at desc, id desc limit %(limit)s""",
        dict(
            ws=workspace_id,
            client=client_id,
            meta=metadata_only,
            upper=cursor.occurred_at if cursor else now,
            cursor_id=cursor.id if cursor else None,
            limit=query.limit + 1,
        ),
    ).fetchall()
    return [
        dict(id=r["id"], occurred_at=r["occurred_at"], client_id=str(client_id), **r["fields"])
        for r in rows
    ]


def client_evidence(
    conn: psycopg.Connection[DictRow],
    workspace_id: WorkspaceId,
    client_id: UUID,
    query: PageQuery,
    now: datetime,
) -> list[DictRow]:
    cursor = query.cursor
    return conn.execute(
        """select e.id, e.client_id, e.quote, e.locator, e.deep_link, r.id as source_record_id,
                  r.occurred_at, r.metadata, p.side::text, p.display_name, p.role_title
             from public.evidence e join public.source_record r
               on r.workspace_id = %(ws)s and r.id = e.source_record_id
             join public.client c on c.workspace_id = %(ws)s and c.id = e.client_id
             left join public.person p on p.workspace_id = %(ws)s and p.id = r.author_person_id
            where e.workspace_id = %(ws)s and e.client_id = %(client)s and c.policy = 'full'
              and r.occurred_at <= %(upper)s::timestamptz and
                  (%(cursor_id)s::uuid is null or r.occurred_at < %(upper)s::timestamptz
                   or e.id < %(cursor_id)s::uuid)
            order by r.occurred_at desc, e.id desc limit %(limit)s""",
        dict(
            ws=workspace_id,
            client=client_id,
            upper=cursor.occurred_at if cursor else now,
            cursor_id=cursor.id if cursor else None,
            limit=query.limit + 1,
        ),
    ).fetchall()


def evidence(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, evidence_id: UUID
) -> DictRow | None:
    return conn.execute(
        """select e.id, e.client_id, e.quote, e.locator, e.deep_link, r.id as source_record_id,
                  r.occurred_at, r.metadata, p.side::text, p.display_name, p.role_title
             from public.evidence e join public.source_record r
               on r.workspace_id = %(ws)s and r.id = e.source_record_id
             join public.client c on c.workspace_id = %(ws)s and c.id = e.client_id
             left join public.person p on p.workspace_id = %(ws)s and p.id = r.author_person_id
            where e.workspace_id = %(ws)s and e.id = %(id)s and c.policy = 'full'""",
        dict(ws=workspace_id, id=evidence_id),
    ).fetchone()


def replay_runs(conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId) -> list[DictRow]:
    return conn.execute(
        """select r.id, r.window_start::text, r.window_end::text, r.step_days,
                  r.status::text, r.finished_at, r.summary,
                  exists(select 1 from public.replay_narrative n
                          where n.workspace_id = %(ws)s and n.replay_run_id = r.id
                            and n.version = 1) as narrated
             from public.replay_run r where r.workspace_id = %(ws)s
            order by r.created_at desc, r.id limit 500""",
        dict(ws=workspace_id),
    ).fetchall()


def replay_narrative(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, run_id: UUID, client_id: UUID
) -> DictRow | None:
    return conn.execute(
        """select n.narrative from public.replay_narrative n
             join public.replay_run r on r.workspace_id = %(ws)s and r.id = n.replay_run_id
            where n.workspace_id = %(ws)s and n.replay_run_id = %(run)s
              and n.client_id = %(client)s and n.version = 1 and r.status = 'succeeded'""",
        dict(ws=workspace_id, run=run_id, client=client_id),
    ).fetchone()


def replay_weeks(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, run_id: UUID, client_id: UUID
) -> list[DictRow]:
    return conn.execute(
        """select as_of::text, risk_state::text as risk, expansion_state::text as expansion,
                  play, strength::text from public.account_assessment
            where workspace_id = %s and replay_run_id = %s and client_id = %s
              and run_kind = 'replay' order by as_of""",
        (workspace_id, run_id, client_id),
    ).fetchall()
