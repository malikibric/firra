"""Workspace-scoped meeting facts and atomic, occurrence-idempotent brief writes."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.assessment.complaints import unresolved_complaints
from firra.brief.meeting import BriefItem, MeetingConfig, MeetingFacts, build_content
from firra.db.signals import SignalRepo
from firra.db.types import WorkspaceId, cross_tenant_maintenance
from firra.memory.rules import has_stated_budget
from firra.memory.rules import load_config as load_memory_config
from firra.memory.unknowns import CardFacts, unknowns
from firra.signals.facts import ExtractionFact, RecordFact


@dataclass(frozen=True)
class StoredBrief:
    id: UUID
    client_name: str
    questions: list[dict[str, Any]]


@dataclass(frozen=True)
class Meeting:
    id: UUID
    client_id: UUID
    starts_at: datetime


@cross_tenant_maintenance("meeting sweep lists workspaces with upcoming eligible client meetings")
def workspaces_with_meetings(
    pool: ConnectionPool[Connection[DictRow]], *, now: datetime, cfg: MeetingConfig
) -> list[WorkspaceId]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            select distinct e.workspace_id
            from public.calendar_event e join public.client c
              on c.workspace_id = e.workspace_id and c.id = e.client_id
            where e.status = 'scheduled' and c.status = 'active' and c.policy <> 'excluded'
              and e.starts_at > %(now)s and e.starts_at <= %(until)s
            order by e.workspace_id
            """,
            {"now": now, "until": now + timedelta(minutes=cfg.lead_minutes)},
        ).fetchall()
    return [WorkspaceId(r["workspace_id"]) for r in rows]


class MeetingRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self.pool = pool

    def upcoming(
        self, *, workspace_id: WorkspaceId, now: datetime, cfg: MeetingConfig
    ) -> list[Meeting]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                select e.id, e.client_id, e.starts_at
                from public.calendar_event e join public.client c
                  on c.workspace_id = e.workspace_id and c.id = e.client_id
                where e.status = 'scheduled' and c.status = 'active' and c.policy <> 'excluded'
                  and e.starts_at > %(now)s and e.starts_at <= %(until)s
                  and e.workspace_id = %(workspace_id)s
                and not exists (select 1 from public.meeting_brief b
                    where b.workspace_id = e.workspace_id and b.calendar_event_id = e.id
                      and b.starts_at = e.starts_at)
                order by e.starts_at, e.id
                """,
                {
                    "workspace_id": workspace_id,
                    "now": now,
                    "until": now + timedelta(minutes=cfg.lead_minutes),
                },
            ).fetchall()
        return [Meeting(r["id"], r["client_id"], r["starts_at"]) for r in rows]

    def load_facts(
        self, *, workspace_id: WorkspaceId, meeting: Meeting, now: datetime, cfg: MeetingConfig
    ) -> MeetingFacts | None:
        memory_cfg = load_memory_config()
        facts = SignalRepo(self.pool).load_facts(
            workspace_id=workspace_id, client_id=meeting.client_id, ref=now, memory_cfg=memory_cfg
        )
        if facts is None or facts.policy == "excluded" or facts.status != "active":
            return None
        base: dict[str, Any] = {
            "workspace_id": workspace_id,
            "client_id": meeting.client_id,
            "now": now,
            "starts_at": meeting.starts_at,
        }
        with self.pool.connection() as conn:
            timeline = conn.execute(
                """
                select count(*)::int as records, max(r.occurred_at) filter (
                    where r.direction = 'in' and r.author_person_id = %(champion)s
                ) as champion_contact, min(r.occurred_at) as earliest
                from public.source_record r
                where r.workspace_id = %(workspace_id)s and r.client_id = %(client_id)s
                  and r.occurred_at <= %(now)s
            """,
                {**base, "champion": next((p.id for p in facts.people if p.is_champion), None)},
            ).fetchone()
            held = conn.execute(
                """
                select max(starts_at) as at from public.calendar_event
                where workspace_id = %(workspace_id)s and client_id = %(client_id)s
                  and status = 'held' and starts_at < %(starts_at)s and starts_at <= %(now)s
            """,
                base,
            ).fetchone()
            if timeline is None or held is None:  # aggregate queries always return a row
                raise RuntimeError("meeting aggregate returned no row")
            count = conn.execute(
                """
                select count(*)::int as n from public.source_record
                where workspace_id = %(workspace_id)s and client_id = %(client_id)s
                  and occurred_at <= %(now)s and (%(held)s::timestamptz is null
                    or occurred_at > %(held)s)
            """,
                {**base, "held": held["at"]},
            ).fetchone()
            people = facts.people
            goal = (
                conn.execute(
                    """
                select id from public.client_goal where workspace_id = %(workspace_id)s
                  and client_id = %(client_id)s and stated_at <= %(now)s limit 1
            """,
                    base,
                ).fetchone()
                if facts.policy == "full"
                else None
            )
            records = (
                conn.execute(
                    """
                select r.id, r.thread_key, r.occurred_at, r.direction::text as direction,
                       r.author_person_id, p.side::text as author_side,
                       r.processing_state = 'done' as processed, r.connection_id
                from public.source_record r left join public.person p
                  on p.workspace_id = r.workspace_id and p.id = r.author_person_id
                where r.workspace_id = %(workspace_id)s and r.client_id = %(client_id)s
                  and r.occurred_at <= %(now)s
            """,
                    base,
                ).fetchall()
                if facts.policy == "full"
                else []
            )
            extractions = (
                conn.execute(
                    """
                select x.id, x.source_record_id, x.kind::text as kind, x.payload, r.occurred_at,
                       r.author_person_id, array_agg(e.id order by e.id) as evidence_ids,
                       array_agg(e.quote order by e.id) as quotes
                from public.extraction x join public.source_record r
                  on r.workspace_id = x.workspace_id and r.id = x.source_record_id
                    and r.client_id = x.client_id
                join public.extraction_evidence xe on xe.workspace_id = x.workspace_id
                  and xe.extraction_id = x.id and xe.client_id = x.client_id
                join public.evidence e on e.workspace_id = xe.workspace_id
                  and e.id = xe.evidence_id and e.client_id = xe.client_id
                where x.workspace_id = %(workspace_id)s and x.client_id = %(client_id)s
                  and r.occurred_at <= %(now)s
                group by x.id, r.occurred_at, r.author_person_id
                order by r.occurred_at desc, x.id
            """,
                    base,
                ).fetchall()
                if facts.policy == "full"
                else []
            )
            commitments = (
                conn.execute(
                    """
                select c.id, c.text, c.owner_side::text as side, c.due_date as due,
                       c.status = 'late' or c.due_date < %(now)s::date as late,
                       array_agg(e.id order by e.id) as evidence_ids
                from public.commitment c join public.commitment_evidence ce
                  on ce.workspace_id = c.workspace_id and ce.commitment_id = c.id
                    and ce.client_id = c.client_id
                join public.evidence e on e.workspace_id = ce.workspace_id
                  and e.id = ce.evidence_id and e.client_id = ce.client_id
                where c.workspace_id = %(workspace_id)s and c.client_id = %(client_id)s
                  and c.status in ('open', 'late') and c.stated_at <= %(now)s
                group by c.id order by c.due_date nulls last, c.id limit %(limit)s
            """,
                    {**base, "limit": cfg.max_items_per_section},
                ).fetchall()
                if facts.policy == "full"
                else []
            )
        loaded = replace(
            facts,
            records=tuple(RecordFact(**r) for r in records),
            extractions=tuple(
                ExtractionFact(
                    r["id"],
                    r["source_record_id"],
                    r["kind"],
                    r["payload"],
                    r["occurred_at"],
                    r["author_person_id"],
                    tuple(r["evidence_ids"]),
                )
                for r in extractions
            ),
        )
        days = (now - timeline["earliest"]).days + 1 if timeline["earliest"] else 1
        complaints = unresolved_complaints(loaded, within_days=max(1, days))
        card = CardFacts(
            has_goal=goal is not None,
            has_decision_maker=any(p.is_decision_maker for p in people),
            renewal_date=facts.renewal_date,
            last_result_at=max(
                (x["occurred_at"] for x in extractions if x["kind"] == "result_reported"),
                default=None,
            ),
            champion_id=next((str(p.id) for p in people if p.is_champion), None),
            last_champion_message_at=timeline["champion_contact"],
            budget_known=any(
                x["kind"] == "renewal_or_budget_talk"
                and any(has_stated_budget(q) for q in x["quotes"])
                for x in extractions
            ),
        )
        return MeetingFacts(
            client_id=meeting.client_id,
            ref=now,
            records=count["n"] if count else 0,
            last_held_at=held["at"],
            unknowns=tuple(unknowns(card, as_of=now.date(), cfg=memory_cfg)),
            commitments=tuple(
                BriefItem(
                    **{**r, "evidence_ids": tuple(r["evidence_ids"]), "late": bool(r["late"])}
                )
                for r in commitments
            ),
            complaints=tuple(
                BriefItem(
                    x.id,
                    x.payload.get("text", ""),
                    x.evidence_ids,
                    subtype=x.payload.get("subtype") or "other",
                )
                for x in complaints
            ),
            stakeholders=tuple(
                BriefItem(
                    x.id, x.payload.get("text", ""), x.evidence_ids, occurred_at=x.occurred_at
                )
                for x in loaded.extractions
                if x.kind == "stakeholder_change"
            ),
            renewal_date=facts.renewal_date,
            notice_period_days=facts.notice_period_days,
        )

    def save(
        self,
        *,
        workspace_id: WorkspaceId,
        meeting: Meeting,
        facts: MeetingFacts,
        now: datetime,
        cfg: MeetingConfig,
        deterministic: bool = False,
    ) -> bool:
        base = {
            "workspace_id": workspace_id,
            "client_id": meeting.client_id,
            "event_id": meeting.id,
            "starts_at": meeting.starts_at,
            "now": now,
            "until": now + timedelta(minutes=cfg.lead_minutes),
        }
        with self.pool.connection() as conn, conn.transaction():
            # Client first, matching the policy RPC lock order. Recheck event and policy on write.
            client = conn.execute(
                """
                select policy::text as policy from public.client
                where workspace_id = %(workspace_id)s and id = %(client_id)s
                  and status = 'active' and policy <> 'excluded' for share
            """,
                base,
            ).fetchone()
            if client is None:
                return False
            event = conn.execute(
                """
                select id from public.calendar_event where workspace_id = %(workspace_id)s
                  and client_id = %(client_id)s and id = %(event_id)s and status = 'scheduled'
                  and starts_at = %(starts_at)s and starts_at > %(now)s and starts_at <= %(until)s
                for share
            """,
                base,
            ).fetchone()
            if event is None:
                return False
            built = build_content(facts, policy=client["policy"], cfg=cfg)
            if built.basis == "evidence" and not built.evidence_ids:
                built = build_content(facts, policy="metadata_only", cfg=cfg)
            brief = conn.execute(
                """
                insert into public.meeting_brief (workspace_id, client_id, calendar_event_id,
                    starts_at, locale, basis, content, version, model, prompt_version)
                values (%(workspace_id)s, %(client_id)s, %(event_id)s, %(starts_at)s,
                    'en', %(basis)s::public.signal_basis, %(content)s, %(version)s,
                    %(model)s, %(prompt)s)
                on conflict (workspace_id, calendar_event_id, starts_at) do nothing returning id
            """,
                {
                    **base,
                    "basis": built.basis,
                    "content": Jsonb(built.content),
                    "version": cfg.version,
                    "model": "deterministic" if deterministic else None,
                    "prompt": "facts.v1" if deterministic else None,
                },
            ).fetchone()
            if brief is None:
                return False
            for evidence_id in built.evidence_ids:
                conn.execute(
                    """
                    insert into public.meeting_brief_evidence
                        (workspace_id, client_id, meeting_brief_id, evidence_id)
                    values (%(workspace_id)s, %(client_id)s, %(brief_id)s, %(evidence_id)s)
                """,
                    {**base, "brief_id": brief["id"], "evidence_id": evidence_id},
                )
        return True

    def stored_for_wording(
        self, *, workspace_id: WorkspaceId, meeting: Meeting
    ) -> StoredBrief | None:
        """The stored brief of this occurrence when it may be reworded: evidence-based, a `full`
        client, with at least one question."""
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                select b.id, c.name, b.content -> 'questions' as questions
                from public.meeting_brief b join public.client c
                  on c.workspace_id = b.workspace_id and c.id = b.client_id
                where b.workspace_id = %(workspace_id)s and b.calendar_event_id = %(event_id)s
                  and b.starts_at = %(starts_at)s and b.basis = 'evidence'
                  and c.policy = 'full' and c.status = 'active'
                  and jsonb_array_length(b.content -> 'questions') > 0
                """,
                {
                    "workspace_id": workspace_id,
                    "event_id": meeting.id,
                    "starts_at": meeting.starts_at,
                },
            ).fetchone()
        if row is None:
            return None
        return StoredBrief(row["id"], row["name"], list(row["questions"]))

    def reword(
        self,
        *,
        workspace_id: WorkspaceId,
        brief: StoredBrief,
        questions: list[dict[str, Any]],
        model: str,
        prompt_version: str,
    ) -> bool:
        """Replaces the questions only while they are still the drafts that were worded and the
        client is still `full`; the basis of every question is unchanged."""
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """
                update public.meeting_brief b
                   set content = jsonb_set(b.content, '{questions}', %(questions)s::jsonb),
                       model = %(model)s, prompt_version = %(prompt_version)s
                  from public.client c
                 where b.workspace_id = %(workspace_id)s and b.id = %(brief_id)s
                   and c.workspace_id = b.workspace_id and c.id = b.client_id
                   and c.policy = 'full' and c.status = 'active'
                   and b.content -> 'questions' = %(drafts)s::jsonb
                returning b.id
                """,
                {
                    "workspace_id": workspace_id,
                    "brief_id": brief.id,
                    "questions": Jsonb(questions),
                    "drafts": Jsonb(brief.questions),
                    "model": model,
                    "prompt_version": prompt_version,
                },
            ).fetchone()
        return row is not None
