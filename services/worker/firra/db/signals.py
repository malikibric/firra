"""Signal storage: facts in, signal rows out (P2 D10). Every statement is scoped by workspace_id.

``load_facts`` reads one client at a time, as of a reference time (P2b D5): nothing dated after
it is included, and people, decision makers, champions, commitment states and needs are computed
as they were then, so replays cannot see the future. A churned client is evaluated as of its churn
date. ``save`` writes one client's signals in a
single transaction: drafts are upserted on (client, type, dedupe key), their evidence links are
replaced, and signals the run no longer produces are resolved, never deleted.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, cast
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.assessment.model import Assessment
from firra.db.assessments import write_live
from firra.db.types import WorkspaceId
from firra.memory.rules import ChampionCandidate, MemoryConfig, choose_champion, commitment_status
from firra.memory.rules import load_config as load_memory_config
from firra.signals.facts import (
    ClientFacts,
    CommitmentFact,
    CommitmentStatus,
    CoverageFact,
    EventFact,
    ExtractionFact,
    NeedFact,
    PersonFact,
    RecordFact,
)
from firra.signals.model import SignalDraft

# The longest look-back any rule needs is 240 days (latency baseline); 400 leaves room.
HISTORY_DAYS = 400
# D4 coverage baseline: records older than 30 and at most 120 days before ref.
BASELINE_DAYS = (30, 120)

_CLIENTS = """
select id, status::text as status
  from public.client
 where workspace_id = %(workspace_id)s and policy <> 'excluded' and status in ('active', 'churned')
 order by id
"""

_CLIENT = """
select status::text as status, policy::text as policy, contract_start, renewal_date,
       notice_period_days, churned_at, service_tags
  from public.client
 where workspace_id = %(workspace_id)s and id = %(client_id)s
"""

# Client-side people known at ref. A decision maker needs an observation made by ref; a manual one
# states a role and counts from the person's first_seen_at. departed_at after ref is not known yet.
_PEOPLE = """
select p.id, p.display_name, p.first_seen_at,
       case when p.departed_at <= %(ref)s then p.departed_at end as departed_at,
       exists (select 1 from public.person_observation o
                where o.workspace_id = p.workspace_id and o.client_id = p.client_id
                  and o.person_id = p.id and o.is_decision_maker
                  and (o.observed_at <= %(ref)s or o.source = 'manual')) as is_decision_maker
  from public.person p
 where p.workspace_id = %(workspace_id)s and p.client_id = %(client_id)s and p.side = 'client'
   and p.merged_into_person_id is null and p.first_seen_at <= %(ref)s
"""

# The memory rule's input (firra/memory/rules.py), counted over the trailing window before ref.
_CHAMPION_COUNTS = """
select author_person_id as id, count(*)::int as message_count
  from public.source_record
 where workspace_id = %(workspace_id)s and client_id = %(client_id)s
   and author_person_id is not null and direction = 'in'
   and occurred_at > %(champion_since)s and occurred_at <= %(ref)s
 group by author_person_id
"""

# Chat records are keyed by channel and thread so a reply is matched within its own conversation
# (the Slack connector stores `channel_id` in the record metadata).
_RECORDS = """
select r.id,
       case when r.kind = 'chat'
            then coalesce(r.metadata->>'channel_id', '') || '/' || coalesce(r.thread_key, '')
            else r.thread_key end as thread_key,
       r.occurred_at, r.direction::text as direction, r.author_person_id,
       p.side::text as author_side, r.processing_state = 'done' as processed, r.connection_id,
       r.kind::text as source
  from public.source_record r
  left join public.person p on p.workspace_id = r.workspace_id and p.id = r.author_person_id
 where r.workspace_id = %(workspace_id)s and r.client_id = %(client_id)s
   and r.kind in ('email', 'chat') and r.occurred_at <= %(ref)s and r.occurred_at > %(since)s
"""

# The firm-side people that are the owner: the identity of an active mail connection's account
# (its email) or of the Slack connection's user id (`external_account_id`).
_OWNERS = """
select distinct pi.person_id
  from public.person_identity pi
  join public.connection c on c.workspace_id = pi.workspace_id and c.status = 'active'
 where pi.workspace_id = %(workspace_id)s
   and ((pi.kind = 'email' and c.provider = 'gmail'
         and lower(pi.value) = lower(coalesce(c.external_account_id, c.account_label)))
     or (pi.kind = 'slack_id' and c.provider = 'slack' and pi.value = c.external_account_id))
"""

_TIMEZONE = "select timezone from public.workspace where id = %(workspace_id)s"

_EXTRACTIONS = """
select x.id, x.source_record_id, x.kind::text as kind, x.payload, r.occurred_at,
       r.author_person_id,
       array_agg(xe.evidence_id order by xe.evidence_id) as evidence_ids
  from public.extraction x
  join public.source_record r on r.workspace_id = x.workspace_id and r.id = x.source_record_id
  join public.extraction_evidence xe on xe.workspace_id = x.workspace_id
   and xe.extraction_id = x.id
 where x.workspace_id = %(workspace_id)s and x.client_id = %(client_id)s
   and r.occurred_at <= %(ref)s and r.occurred_at > %(since)s
 group by x.id, r.occurred_at, r.author_person_id
"""

_COMMITMENTS = """
select c.id, c.owner_side::text as owner_side, c.due_date,
       case when c.closed_at <= %(ref)s then c.status::text else 'open' end as status,
       coalesce(array_agg(ce.evidence_id order by ce.evidence_id)
                filter (where ce.evidence_id is not null), '{}') as evidence_ids
  from public.commitment c
  left join public.commitment_evidence ce on ce.workspace_id = c.workspace_id
   and ce.commitment_id = c.id
 where c.workspace_id = %(workspace_id)s and c.client_id = %(client_id)s
   and c.stated_at <= %(ref)s
 group by c.id
"""

_EVENTS = """
select e.starts_at, e.status::text as status, e.attendee_count
  from public.calendar_event e
 where e.workspace_id = %(workspace_id)s and e.client_id = %(client_id)s
   and e.starts_at <= %(ref)s and e.starts_at > %(since)s
"""

_NEEDS = """
select n.id, n.workspace_service_id, s.name as service_name, n.status::text as status,
       n.last_seen_at, n.mention_count,
       coalesce(array_agg(ne.evidence_id order by ne.evidence_id)
                filter (where ne.evidence_id is not null), '{}') as evidence_ids
  from public.latent_need n
  left join public.workspace_service s on s.workspace_id = n.workspace_id
   and s.id = n.workspace_service_id
  left join public.latent_need_evidence ne on ne.workspace_id = n.workspace_id
   and ne.client_id = n.client_id and ne.latent_need_id = n.id
 where n.workspace_id = %(workspace_id)s and n.client_id = %(client_id)s
   and n.status in ('open', 'addressed') and n.last_seen_at <= %(ref)s
 group by n.id, s.name
"""

# D4: connections that carried the client's baseline records (days 31-120 before ref) and how far
# each is synced. Current sync state on purpose: it says how complete the stored data is.
_COVERAGE = """
select c.id as connection_id, c.status::text as status,
       case when c.mode = 'upload' then
              (select max(u.occurred_at) from public.source_record u
                where u.workspace_id = c.workspace_id and u.connection_id = c.id)
            when c.status <> 'active' then c.last_synced_at end as synced_until
  from public.connection c
 where c.workspace_id = %(workspace_id)s
   and exists (select 1 from public.source_record r
                where r.workspace_id = c.workspace_id and r.connection_id = c.id
                  and r.client_id = %(client_id)s
                  and r.occurred_at > %(baseline_since)s and r.occurred_at <= %(baseline_until)s)
 order by c.id
"""

# Same lock order as set_client_policy (client row first); a write the policy no longer allows is
# skipped.
_LOCK_CLIENT = """
select policy::text as policy from public.client
 where workspace_id = %(workspace_id)s and id = %(client_id)s for share
"""

_UPSERT = """
insert into public.signal (workspace_id, client_id, type, severity, occurred_at, rule_version,
                           basis, inputs, dedupe_key, family)
values (%(workspace_id)s, %(client_id)s, %(type)s, %(severity)s::public.signal_severity,
        %(occurred_at)s, %(rule_version)s, %(basis)s::public.signal_basis, %(inputs)s,
        %(dedupe_key)s, %(family)s)
on conflict (workspace_id, client_id, type, dedupe_key) do update
   set severity = excluded.severity, occurred_at = excluded.occurred_at,
       rule_version = excluded.rule_version, basis = excluded.basis, inputs = excluded.inputs,
       family = excluded.family, resolved_at = null
returning id
"""

_EXISTING = """
select id, type, dedupe_key, severity::text as severity, resolved_at is not null as resolved
  from public.signal
 where workspace_id = %(workspace_id)s and client_id = %(client_id)s
"""

# Append-only history (P2b D1): one row per change of a signal, never for an unchanged one.
_EVENT = """
insert into public.signal_event (workspace_id, client_id, signal_id, change, severity, as_of,
                                 rule_version, inputs, job_id)
values (%(workspace_id)s, %(client_id)s, %(signal_id)s, %(change)s::public.signal_change,
        %(severity)s::public.signal_severity, %(as_of)s, %(rule_version)s, %(inputs)s,
        %(job_id)s)
"""

_DROP_LINKS = """
delete from public.signal_evidence
 where workspace_id = %(workspace_id)s and signal_id = %(signal_id)s
   and evidence_id <> all(%(evidence_ids)s::uuid[])
"""

_ADD_LINK = """
insert into public.signal_evidence (workspace_id, signal_id, evidence_id, client_id)
values (%(workspace_id)s, %(signal_id)s, %(evidence_id)s, %(client_id)s)
on conflict (signal_id, evidence_id) do nothing
"""

_RESOLVE = """
update public.signal set resolved_at = now()
 where workspace_id = %(workspace_id)s and client_id = %(client_id)s and id = any(%(ids)s)
"""


@dataclass(frozen=True, slots=True)
class ClientRef:
    id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class SaveResult:
    written: int
    resolved: int
    events: int = 0
    assessment_id: UUID | None = None


class SignalRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def clients(self, *, workspace_id: WorkspaceId) -> list[ClientRef]:
        """Clients worth evaluating: active or churned, never excluded."""
        with self._pool.connection() as conn:
            rows = conn.execute(_CLIENTS, {"workspace_id": workspace_id}).fetchall()
        return [ClientRef(id=r["id"], status=r["status"]) for r in rows]

    def load_facts(
        self,
        *,
        workspace_id: WorkspaceId,
        client_id: UUID,
        ref: datetime,
        memory_cfg: MemoryConfig | None = None,
    ) -> ClientFacts | None:
        """Facts as of ``ref``; for a churned client ``ref`` is capped at its churn date."""
        cfg = memory_cfg or load_memory_config()
        base = {"workspace_id": workspace_id, "client_id": client_id}
        with self._pool.connection() as conn:
            client = conn.execute(_CLIENT, base).fetchone()
            if client is None:
                return None
            if client["status"] == "churned" and client["churned_at"] is not None:
                # churned_at is a date: the client's last day counts in full.
                ref = min(ref, datetime.combine(client["churned_at"], time.max, tzinfo=UTC))
            window: dict[str, Any] = {
                **base,
                "ref": ref,
                "since": ref - timedelta(days=HISTORY_DAYS),
                "champion_since": ref - timedelta(days=cfg.champion_window_days),
                "baseline_since": ref - timedelta(days=BASELINE_DAYS[1]),
                "baseline_until": ref - timedelta(days=BASELINE_DAYS[0]),
            }
            people = conn.execute(_PEOPLE, window).fetchall()
            counts = conn.execute(_CHAMPION_COUNTS, window).fetchall()
            records = conn.execute(_RECORDS, window).fetchall()
            extractions = conn.execute(_EXTRACTIONS, window).fetchall()
            commitments = conn.execute(_COMMITMENTS, window).fetchall()
            events = conn.execute(_EVENTS, window).fetchall()
            needs = conn.execute(_NEEDS, window).fetchall()
            coverage = conn.execute(_COVERAGE, window).fetchall()
            owners = conn.execute(_OWNERS, window).fetchall()
            zone = conn.execute(_TIMEZONE, window).fetchone()
        count_by_id = {r["id"]: r["message_count"] for r in counts}
        champion = choose_champion(
            [
                ChampionCandidate(r["id"], count_by_id.get(r["id"], 0), r["first_seen_at"])
                for r in people
            ],
            cfg,
        )
        return ClientFacts(
            client_id=client_id,
            status=client["status"],
            policy=client["policy"],
            contract_start=client["contract_start"],
            renewal_date=client["renewal_date"],
            notice_period_days=client["notice_period_days"],
            ref=ref,
            service_tags=tuple(client["service_tags"] or ()),
            timezone=(zone["timezone"] if zone else None) or "UTC",
            owner_person_ids=frozenset(r["person_id"] for r in owners),
            people=tuple(
                PersonFact(
                    id=r["id"],
                    name=r["display_name"],
                    is_decision_maker=r["is_decision_maker"],
                    is_champion=r["id"] == champion,
                    departed_at=r["departed_at"],
                    first_seen_at=r["first_seen_at"],
                )
                for r in sorted(people, key=lambda r: str(r["id"]))
            ),
            records=tuple(
                RecordFact(
                    id=r["id"],
                    thread_key=r["thread_key"],
                    occurred_at=r["occurred_at"],
                    direction=r["direction"],
                    author_person_id=r["author_person_id"],
                    author_side=r["author_side"],
                    processed=r["processed"],
                    connection_id=r["connection_id"],
                    source=r["source"],
                )
                for r in sorted(records, key=lambda r: (r["occurred_at"], str(r["id"])))
            ),
            extractions=tuple(
                ExtractionFact(
                    id=r["id"],
                    record_id=r["source_record_id"],
                    kind=r["kind"],
                    payload=r["payload"],
                    occurred_at=r["occurred_at"],
                    author_person_id=r["author_person_id"],
                    evidence_ids=tuple(r["evidence_ids"]),
                )
                for r in sorted(extractions, key=lambda r: (r["occurred_at"], str(r["id"])))
            ),
            commitments=tuple(
                CommitmentFact(
                    id=r["id"],
                    owner_side=r["owner_side"],
                    due_date=r["due_date"],
                    status=cast(
                        CommitmentStatus, commitment_status(r["status"], r["due_date"], ref.date())
                    ),
                    evidence_ids=tuple(r["evidence_ids"]),
                )
                for r in sorted(commitments, key=lambda r: str(r["id"]))
            ),
            events=tuple(
                EventFact(
                    starts_at=r["starts_at"],
                    status=r["status"],
                    attendee_count=r["attendee_count"],
                )
                for r in sorted(events, key=lambda r: (r["starts_at"], r["status"]))
            ),
            needs=tuple(
                NeedFact(
                    id=r["id"],
                    workspace_service_id=r["workspace_service_id"],
                    service_name=r["service_name"],
                    status=r["status"],
                    last_seen_at=r["last_seen_at"],
                    mention_count=r["mention_count"],
                    evidence_ids=tuple(r["evidence_ids"]),
                )
                for r in sorted(needs, key=lambda r: str(r["id"]))
            ),
            coverage=tuple(
                CoverageFact(
                    connection_id=r["connection_id"],
                    status=r["status"],
                    synced_until=r["synced_until"],
                )
                for r in coverage
            ),
        )

    def save(
        self,
        *,
        workspace_id: WorkspaceId,
        client_id: UUID,
        drafts: list[SignalDraft],
        rule_version: str,
        as_of: datetime | None = None,
        job_id: UUID | None = None,
        assessment: Assessment | None = None,
        assessment_version: str | None = None,
    ) -> SaveResult:
        """One client's run in one transaction: signals, their change history, the assessment.

        Drafts are upserted on (type, dedupe key); every change becomes a ``signal_event``
        (opened, severity_changed, reopened, resolved); signals the run no longer produces are
        resolved, never deleted. The client row is locked first, like ``set_client_policy``.
        """
        base = {"workspace_id": workspace_id, "client_id": client_id}
        at = as_of or datetime.now(UTC)
        event = {**base, "as_of": at, "rule_version": rule_version, "job_id": job_id}
        produced: dict[tuple[str, str], UUID] = {}
        events = 0
        with self._pool.connection() as conn:
            client = conn.execute(_LOCK_CLIENT, base).fetchone()
            if client is None or client["policy"] == "excluded":
                return SaveResult(written=0, resolved=0)
            if client["policy"] == "metadata_only":
                # The policy changed after the facts were read: quotes are gone, keep metadata only.
                drafts = [draft for draft in drafts if not draft.evidence_ids]
                if assessment is not None and assessment.evidence_ids():
                    assessment = None  # recomputed from metadata on the next run
            existing = {
                (r["type"], r["dedupe_key"]): r for r in conn.execute(_EXISTING, base).fetchall()
            }
            for draft in drafts:
                inputs = Jsonb(draft.inputs) if draft.inputs is not None else None
                signal = conn.execute(
                    _UPSERT,
                    {
                        **base,
                        "type": draft.type,
                        "severity": draft.severity,
                        "occurred_at": draft.occurred_at,
                        "rule_version": rule_version,
                        "basis": draft.basis,
                        "inputs": inputs,
                        "dedupe_key": draft.dedupe_key,
                        "family": draft.family,
                    },
                ).fetchone()
                if signal is None:  # pragma: no cover - returning id
                    raise RuntimeError("signal upsert returned no id")
                key = (draft.type, draft.dedupe_key)
                produced[key] = signal["id"]
                before = existing.get(key)
                change = (
                    "opened"
                    if before is None
                    else "reopened"
                    if before["resolved"]
                    else "severity_changed"
                    if before["severity"] != draft.severity
                    else None
                )
                if change is not None:
                    conn.execute(
                        _EVENT,
                        {
                            **event,
                            "signal_id": signal["id"],
                            "change": change,
                            "severity": draft.severity,
                            "inputs": inputs,
                        },
                    )
                    events += 1
                link = {**base, "signal_id": signal["id"]}
                conn.execute(_DROP_LINKS, {**link, "evidence_ids": list(draft.evidence_ids)})
                for evidence_id in draft.evidence_ids:
                    conn.execute(_ADD_LINK, {**link, "evidence_id": evidence_id})
            stale = [
                row
                for key, row in sorted(existing.items())
                if key not in produced and not row["resolved"]
            ]
            if stale:
                conn.execute(_RESOLVE, {**base, "ids": [row["id"] for row in stale]})
                for row in stale:
                    conn.execute(
                        _EVENT,
                        {
                            **event,
                            "signal_id": row["id"],
                            "change": "resolved",
                            "severity": row["severity"],
                            "inputs": None,
                        },
                    )
                    events += 1
            assessment_id = None
            if assessment is not None:
                assessment_id = write_live(
                    conn,
                    base,
                    assessment,
                    signal_ids=produced,
                    assessment_version=assessment_version or "",
                    rule_version=rule_version,
                )
        return SaveResult(
            written=len(drafts),
            resolved=len(stale),
            events=events,
            assessment_id=assessment_id,
        )
