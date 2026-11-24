"""Workspace-scoped local read queries, executed as the authenticated owner through RLS."""

from datetime import datetime, timedelta
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

from firra.db.types import WorkspaceId


def clients(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, status: str = "all"
) -> list[DictRow]:
    return conn.execute(
        """select c.id, c.name, c.status::text, c.policy::text, c.mrr, c.currency,
                  c.renewal_date::text, c.notice_period_days,
                  (select to_jsonb(a) from public.account_assessment a
                    where a.workspace_id = %(ws)s and a.client_id = c.id and a.run_kind = 'live'
                    order by a.as_of desc limit 1) as assessment
             from public.client c
            where c.workspace_id = %(ws)s and c.policy in ('full', 'metadata_only')
              and (%(status)s = 'all' or c.status::text = %(status)s)
            order by c.name, c.id limit 500""",
        {"ws": workspace_id, "status": status},
    ).fetchall()


def attention(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, now: datetime
) -> list[DictRow]:
    return conn.execute(
        """select a.client_id::text, a.as_of::text, a.risk_state::text,
                  a.previous_risk_state::text, a.state_since::text, a.expansion_state::text,
                  a.strength::text, a.play, a.drivers
             from public.account_assessment a
            where a.workspace_id = %s and a.run_kind = 'live' and a.as_of >= %s
            order by a.as_of desc limit 5000""",
        (workspace_id, (now - timedelta(days=60)).date()),
    ).fetchall()


def changes_assessments(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, since: str, now: datetime
) -> list[DictRow]:
    return conn.execute(
        """select a.client_id::text, a.as_of::text, a.risk_state::text,
                  a.previous_risk_state::text, a.expansion_state::text
             from public.account_assessment a
            where a.workspace_id = %(ws)s and a.run_kind = 'live' and a.as_of <= %(today)s
              and (a.as_of >= %(since)s or a.id in
                   (select distinct on (b.client_id) b.id from public.account_assessment b
                     where b.workspace_id = %(ws)s and b.run_kind = 'live' and b.as_of < %(since)s
                     order by b.client_id, b.as_of desc))
            order by a.as_of, a.id""",
        {"ws": workspace_id, "since": since, "today": now.date()},
    ).fetchall()


def changes_events(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, since: str, now: datetime
) -> list[DictRow]:
    return conn.execute(
        """select e.id::text, e.signal_id::text, e.client_id::text, e.change::text,
                  e.severity::text, e.as_of, e.created_at,
                  jsonb_build_object('type', s.type, 'family', s.family, 'basis', s.basis,
                    'signal_evidence', coalesce((select jsonb_agg(
                       jsonb_build_object('evidence_id', l.evidence_id))
                       from public.signal_evidence l
                      where l.workspace_id = %(ws)s and l.signal_id = s.id), '[]'::jsonb)) as signal
             from public.signal_event e join public.signal s
               on s.workspace_id = %(ws)s and s.id = e.signal_id
            where e.workspace_id = %(ws)s and e.as_of <= %(now)s
              and (e.as_of >= %(since)s::date or e.id in
                   (select distinct on (b.signal_id) b.id from public.signal_event b
                     where b.workspace_id = %(ws)s and b.as_of < %(since)s::date
                     order by b.signal_id, b.as_of desc, b.created_at desc, b.id desc))
            order by e.as_of, e.created_at, e.id""",
        {"ws": workspace_id, "since": since, "now": now},
    ).fetchall()


def client(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, client_id: UUID
) -> DictRow | None:
    return conn.execute(
        """select id, name, status::text, policy::text, mrr, currency, renewal_date::text,
                  notice_period_days from public.client
            where workspace_id = %s and id = %s and policy in ('full', 'metadata_only')""",
        (workspace_id, client_id),
    ).fetchone()


def detail_assessments(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, client_id: UUID
) -> tuple[DictRow | None, list[DictRow]]:
    params = (workspace_id, client_id)
    latest = conn.execute(
        """select as_of::text, risk_state::text, previous_risk_state::text, state_since::text,
                  expansion_state::text, strength::text, play, drivers, caveats
             from public.account_assessment
            where workspace_id = %s and client_id = %s and run_kind = 'live'
            order by as_of desc limit 1""",
        params,
    ).fetchone()
    history = conn.execute(
        """select as_of::text, risk_state::text, previous_risk_state::text
             from public.account_assessment
            where workspace_id = %s and client_id = %s and run_kind = 'live'
              and previous_risk_state is not null and previous_risk_state <> risk_state
            order by as_of desc limit 20""",
        params,
    ).fetchall()
    return latest, history


def detail_parts(
    conn: psycopg.Connection[DictRow],
    workspace_id: WorkspaceId,
    client_id: UUID,
    *,
    metadata_only: bool,
) -> DictRow:
    params = {"ws": workspace_id, "client": client_id}
    stakeholders = conn.execute(
        """select display_name, role_title, is_decision_maker, is_champion, last_seen_at
             from public.person where workspace_id = %(ws)s and client_id = %(client)s
              and side = 'client' and merged_into_person_id is null
            order by display_name, id limit 500""",
        params,
    ).fetchall()
    outcomes = conn.execute(
        """select event::text, occurred_at::text, notice_at::text, first_known_at::text,
                  amount_change, currency from public.outcome
            where workspace_id = %(ws)s and client_id = %(client)s
            order by occurred_at desc, id limit 500""",
        params,
    ).fetchall()
    card = conn.execute(
        """select content from public.client_card
            where workspace_id = %(ws)s and client_id = %(client)s
            order by version desc limit 1""",
        params,
    ).fetchone()
    parts: DictRow = dict(
        stakeholders=stakeholders,
        outcomes=outcomes,
        cardContent=card["content"] if card else None,
        commitments=[],
        goals=[],
        needs=[],
    )
    if metadata_only:
        return parts
    parts["commitments"] = conn.execute(
        """select c.owner_side::text, c.text, c.due_date::text, c.status::text,
                  coalesce((select jsonb_agg(to_jsonb(l)) from
                    (select e.evidence_id, e.created_at from public.commitment_evidence e
                      where e.workspace_id = %(ws)s and e.commitment_id = c.id
                      order by e.created_at desc, e.evidence_id limit 5) l),
                     '[]'::jsonb) as commitment_evidence
             from public.commitment c where c.workspace_id = %(ws)s and c.client_id = %(client)s
              and c.status in ('open', 'late') order by c.due_date, c.id limit 500""",
        params,
    ).fetchall()
    parts["goals"] = conn.execute(
        """select c.text, c.metric, c.stated_at,
                  coalesce((select jsonb_agg(to_jsonb(l)) from
                    (select e.evidence_id, e.created_at from public.client_goal_evidence e
                      where e.workspace_id = %(ws)s and e.client_goal_id = c.id
                      order by e.created_at desc, e.evidence_id limit 5) l),
                     '[]'::jsonb) as client_goal_evidence
             from public.client_goal c where c.workspace_id = %(ws)s and c.client_id = %(client)s
            order by c.stated_at desc, c.id limit 500""",
        params,
    ).fetchall()
    parts["needs"] = conn.execute(
        """select c.topic, c.mention_count, c.thread_count, c.first_seen_at, c.last_seen_at,
                  coalesce((select jsonb_agg(to_jsonb(l)) from
                    (select e.evidence_id, e.created_at from public.latent_need_evidence e
                      where e.workspace_id = %(ws)s and e.latent_need_id = c.id
                      order by e.created_at desc, e.evidence_id limit 5) l),
                     '[]'::jsonb) as latent_need_evidence
             from public.latent_need c where c.workspace_id = %(ws)s and c.client_id = %(client)s
              and c.status = 'open' order by c.last_seen_at desc, c.id limit 500""",
        params,
    ).fetchall()
    return parts


def meetings(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, now: datetime
) -> tuple[list[DictRow], list[DictRow], list[DictRow]]:
    briefs = conn.execute(
        """select id::text, calendar_event_id::text, client_id::text,
                  starts_at::text, basis::text, content
             from public.meeting_brief where workspace_id = %s and starts_at > %s
            order by starts_at limit 50""",
        (workspace_id, now),
    ).fetchall()
    events = conn.execute(
        """select id::text, status::text, starts_at::text from public.calendar_event
            where workspace_id = %s and id = any(%s)""",
        (workspace_id, [b["calendar_event_id"] for b in briefs]),
    ).fetchall()
    client_rows = conn.execute(
        """select id::text, name, status::text, policy::text from public.client
            where workspace_id = %s and id = any(%s)""",
        (workspace_id, [b["client_id"] for b in briefs]),
    ).fetchall()
    return briefs, events, client_rows


def setup_clients(conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId) -> list[DictRow]:
    return conn.execute(
        """select c.id, c.name, c.status::text, c.policy::text, c.created_at,
                  coalesce((select jsonb_agg(d.domain order by d.domain) from public.client_domain d
                             where d.workspace_id = %(ws)s and d.client_id = c.id),
                           '[]'::jsonb) as domains
             from public.client c where c.workspace_id = %(ws)s order by c.name""",
        {"ws": workspace_id},
    ).fetchall()


def audit(conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, page: int) -> list[DictRow]:
    return conn.execute(
        """select id, created_at, action, actor_type::text, actor_id, target_type, target_id
             from public.audit_log where workspace_id = %s
            order by created_at desc, id desc limit 51 offset %s""",
        (workspace_id, (page - 1) * 50),
    ).fetchall()


def visible_clients(conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId) -> list[DictRow]:
    return conn.execute(
        """select id::text, name, status::text, policy::text from public.client
            where workspace_id = %s and status = 'active'
              and policy in ('full', 'metadata_only')""",
        (workspace_id,),
    ).fetchall()
