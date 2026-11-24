"""Workspace-scoped routine state and atomic policy-aware card writes."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId, cross_tenant_maintenance
from firra.routines.config import Schedule, load_config
from firra.routines.model import ClientContext, Decision, Prior
from firra.routines.schedule import is_due
from firra.routines.words import render

STALE_RUN_MINUTES = 15


@dataclass(frozen=True)
class DueRoutine:
    workspace_id: WorkspaceId
    routine_id: UUID
    local_date: date
    unknown_timezone: bool


class RoutinesRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self.pool = pool

    def ensure_routines(self, *, workspace_id: WorkspaceId) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """
                insert into public.routine (workspace_id, kind, definition_version)
                select %(workspace_id)s, kind::public.routine_kind, %(version)s
                from unnest(array['morning_scan','commitment_watch','pre_meeting_brief']) kind
                on conflict (workspace_id, kind) do nothing
            """,
                {"workspace_id": workspace_id, "version": load_config().version},
            )

    def claim_run(
        self, *, workspace_id: WorkspaceId, routine_id: UUID, scheduled_for: date
    ) -> dict[str, Any] | None:
        params = {
            "workspace_id": workspace_id,
            "routine_id": routine_id,
            "day": scheduled_for,
            "stale_minutes": STALE_RUN_MINUTES,
        }
        with self.pool.connection() as conn:
            conn.execute(
                """
                insert into public.routine_run (workspace_id, routine_id, scheduled_for)
                select workspace_id, id, %(day)s from public.routine
                where workspace_id = %(workspace_id)s and id = %(routine_id)s
                  and kind <> 'pre_meeting_brief'
                on conflict (routine_id, scheduled_for) do nothing
            """,
                params,
            )
            row = conn.execute(
                """
                update public.routine_run rr set status = 'running', started_at = now(),
                    finished_at = null, error_class = null, counts = '{}'
                from public.routine r where rr.workspace_id = %(workspace_id)s
                  and rr.routine_id = %(routine_id)s and rr.scheduled_for = %(day)s
                  and (rr.status in ('queued','failed') or (rr.status = 'running'
                    and rr.started_at < now() - make_interval(mins => %(stale_minutes)s)))
                  and r.workspace_id = rr.workspace_id
                  and r.id = rr.routine_id and r.kind <> 'pre_meeting_brief'
                returning rr.id, r.kind::text, r.enabled
            """,
                params,
            ).fetchone()
        return dict(row) if row else None

    def finish_run(
        self,
        *,
        workspace_id: WorkspaceId,
        run_id: UUID,
        status: Literal["succeeded", "failed"],
        counts: dict[str, int],
        error_class: str | None = None,
    ) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """
                update public.routine_run set status = %(status)s::public.routine_run_status,
                    counts = %(counts)s, error_class = %(error_class)s, finished_at = now()
                where workspace_id = %(workspace_id)s and id = %(run_id)s
            """,
                {
                    "workspace_id": workspace_id,
                    "run_id": run_id,
                    "status": status,
                    "counts": Jsonb(counts),
                    "error_class": error_class,
                },
            )

    def contexts(
        self, *, workspace_id: WorkspaceId, client_ids: Sequence[UUID], as_of: date
    ) -> dict[UUID, ClientContext]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                select c.id, c.policy::text, coalesce(a.risk_state::text, 'stable') as risk,
                  (c.renewal_date - c.notice_period_days - %(as_of)s::date) as days_to_notice
                from public.client c left join lateral (
                  select risk_state from public.account_assessment
                  where workspace_id = c.workspace_id and client_id = c.id
                    and run_kind = 'live' and as_of <= %(as_of)s order by as_of desc limit 1
                ) a on true where c.workspace_id = %(workspace_id)s
                  and c.id = any(%(ids)s::uuid[]) and c.policy <> 'excluded' and c.status = 'active'
            """,
                {"workspace_id": workspace_id, "ids": list(client_ids), "as_of": as_of},
            )
            return {
                r["id"]: ClientContext(r["id"], r["policy"], r["risk"], r["days_to_notice"])
                for r in rows
            }

    def priors(
        self, *, workspace_id: WorkspaceId, client_ids: Sequence[UUID]
    ) -> dict[UUID, list[Prior]]:
        result: dict[UUID, list[Prior]] = {}
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                select d.client_id, d.kind::text, d.reason_code, d.surfaced_on,
                    d.inputs, c.policy::text, f.verdict::text, array(select e.evidence_id
                      from public.decision_evidence e where e.workspace_id = d.workspace_id
                      and e.decision_card_id = d.id order by e.evidence_id) as evidence_ids
                from public.decision_card d join public.client c
                  on c.workspace_id = d.workspace_id and c.id = d.client_id
                left join lateral (select verdict from public.decision_feedback
                  where workspace_id = d.workspace_id and decision_card_id = d.id
                  order by updated_at desc, id desc limit 1) f on true
                where d.workspace_id = %(workspace_id)s and d.client_id = any(%(ids)s::uuid[])
                  and c.policy <> 'excluded' and d.reason_code is not null
                  and d.importance is not null
                  and d.surfaced_on is not null
            """,
                {"workspace_id": workspace_id, "ids": list(client_ids)},
            )
            for r in rows:
                result.setdefault(r["client_id"], []).append(
                    Prior(
                        r["kind"],
                        r["reason_code"],
                        r["verdict"],
                        r["surfaced_on"],
                        frozenset(r["evidence_ids"]) if r["policy"] == "full" else frozenset(),
                        r["inputs"]["risk_rank"],
                    )
                )
        return result

    def important_used(self, *, workspace_id: WorkspaceId, week_start: date) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                select count(*) as n from public.decision_card d join public.client c
                  on c.workspace_id = d.workspace_id and c.id = d.client_id
                where d.workspace_id = %(workspace_id)s and d.importance = 'important'
                  and d.surfaced_on >= %(start)s and d.surfaced_on < %(end)s
                  and c.policy <> 'excluded'
            """,
                {
                    "workspace_id": workspace_id,
                    "start": week_start,
                    "end": week_start + timedelta(days=7),
                },
            ).fetchone()
        return int(row["n"]) if row else 0

    def latest_play(
        self, *, workspace_id: WorkspaceId, client_id: UUID, as_of: date
    ) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                select a.play, a.strength::text from public.account_assessment a
                join public.client c on c.workspace_id = a.workspace_id and c.id = a.client_id
                where a.workspace_id = %(workspace_id)s and a.client_id = %(client_id)s
                  and a.run_kind = 'live' and a.as_of <= %(as_of)s and c.policy <> 'excluded'
                order by a.as_of desc limit 1
            """,
                {"workspace_id": workspace_id, "client_id": client_id, "as_of": as_of},
            ).fetchone()
        return dict(row) if row else None

    def write_cards(
        self, *, workspace_id: WorkspaceId, decisions: Sequence[Decision], run_id: UUID, as_of: date
    ) -> dict[str, int]:
        written = skipped = 0
        with self.pool.connection() as conn:
            for priority, decision in enumerate(decisions, 1):
                params: dict[str, Any] = {
                    "workspace_id": workspace_id,
                    "client_id": decision.client_id,
                    "as_of": as_of,
                }
                client = conn.execute(
                    """
                    select policy::text from public.client where workspace_id = %(workspace_id)s
                      and id = %(client_id)s and policy <> 'excluded'
                      and status = 'active' for share
                """,
                    params,
                ).fetchone()
                if client is None:
                    continue
                assessment = conn.execute(
                    """
                    select play, strength::text from public.account_assessment
                    where workspace_id = %(workspace_id)s and client_id = %(client_id)s
                      and run_kind = 'live' and as_of <= %(as_of)s order by as_of desc limit 1
                """,
                    params,
                ).fetchone()
                if assessment is None:
                    skipped += 1
                    continue
                if client["policy"] == "metadata_only":
                    decision = replace(decision, evidence_ids=frozenset())
                text = render(decision)
                row = conn.execute(
                    """
                    insert into public.decision_card (workspace_id, client_id, week, kind, title,
                      body, locale, score, priority, inputs, model, prompt_version, dedupe_key,
                      play, facts, interpretation, recommendation, strength, importance,
                      demoted_reason, reason_code, surfaced_on, routine_run_id)
                    values (%(workspace_id)s, %(client_id)s, %(week)s,
                      %(kind)s::public.decision_kind,
                      %(title)s, %(body)s, 'en', %(score)s, %(priority)s, %(inputs)s,
                      'deterministic', 'facts.v1', %(dedupe)s, %(play)s, %(facts)s,
                      %(interpretation)s, %(recommendation)s,
                      %(strength)s::public.evidence_strength,
                      %(importance)s::public.card_importance, %(demoted)s, %(reason)s,
                      %(as_of)s, %(run_id)s)
                    on conflict (workspace_id, week, dedupe_key) do nothing returning id
                """,
                    {
                        **params,
                        "week": as_of - timedelta(days=as_of.weekday()),
                        "kind": decision.kind,
                        "title": text.title,
                        "body": text.body,
                        "score": len(decisions) - priority + 1,
                        "priority": priority,
                        "inputs": Jsonb(
                            {
                                "risk_rank": decision.risk_rank,
                                "evidence_ids": sorted(map(str, decision.evidence_ids)),
                                "also": decision.also,
                                "reason_code": decision.reason_code,
                            }
                        ),
                        "dedupe": f"routine:{decision.kind}:{decision.client_id}:{as_of}",
                        **dict(assessment),
                        "facts": Jsonb(text.facts),
                        "interpretation": text.interpretation,
                        "recommendation": text.recommendation,
                        "importance": decision.importance,
                        "demoted": decision.demoted_reason,
                        "reason": decision.reason_code,
                        "run_id": run_id,
                    },
                )
                card = row.fetchone()
                if card is None:
                    continue
                for evidence_id in sorted(decision.evidence_ids):
                    conn.execute(
                        """
                        insert into public.decision_evidence
                          (workspace_id, decision_card_id, client_id, evidence_id)
                        values (%(workspace_id)s, %(card_id)s, %(client_id)s, %(evidence_id)s)
                    """,
                        {**params, "card_id": card["id"], "evidence_id": evidence_id},
                    )
                written += 1
        return {"written": written, "skipped_no_assessment": skipped}

    @cross_tenant_maintenance("routine sweep reads enabled schedules and workspace timezones")
    def due_routines(self, *, now: datetime) -> list[DueRoutine]:
        config = load_config()
        result = []
        with self.pool.connection() as conn:
            rows = conn.execute("""
                select r.workspace_id, r.id, r.kind::text, w.timezone,
                    (select max(rr.scheduled_for) from public.routine_run rr
                     where rr.workspace_id = r.workspace_id and rr.routine_id = r.id) as last_date
                from public.routine r join public.workspace w on w.id = r.workspace_id
                where r.enabled and r.kind in ('morning_scan', 'commitment_watch') order by r.id
            """).fetchall()
        for row in rows:
            schedule: Schedule = getattr(config.schedules, row["kind"])
            day = is_due(now, row["timezone"], schedule, row["last_date"])
            if day is None:
                continue
            unknown = False
            try:
                ZoneInfo(row["timezone"])
            except (ZoneInfoNotFoundError, ValueError):
                unknown = True
            result.append(DueRoutine(WorkspaceId(row["workspace_id"]), row["id"], day, unknown))
        return result
