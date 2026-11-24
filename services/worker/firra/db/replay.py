"""Replay run storage (P2b D8). Every statement is scoped by workspace_id.

A run's assessments are rows with ``run_kind = replay`` and the run id; a rerun deletes them first,
so the task is idempotent. Signals and signal events are never written by a replay.
"""

from datetime import date
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.assessment.model import Assessment
from firra.assessment.narrative import StepRow, build_narrative, narrative_to_json
from firra.db.types import WorkspaceId
from firra.local.bodycrypt import read_field

_CREATE = """
insert into public.replay_run (workspace_id, window_start, window_end, step_days)
values (%(workspace_id)s, %(window_start)s, %(window_end)s, %(step_days)s)
returning id
"""

_START = """
update public.replay_run
   set status = 'running', started_at = now(), finished_at = null, summary = null,
       error_class = null, assessment_version = %(assessment_version)s,
       rule_version = %(rule_version)s, job_id = coalesce(%(job_id)s, job_id), updated_at = now()
 where workspace_id = %(workspace_id)s and id = %(run_id)s
returning window_start, window_end, step_days
"""

_CLEAR = """
delete from public.account_assessment
 where workspace_id = %(workspace_id)s and run_kind = 'replay' and replay_run_id = %(run_id)s
"""

_LOCK_CLIENT = """
select policy::text as policy from public.client
 where workspace_id = %(workspace_id)s and id = %(client_id)s for share
"""

_INSERT = """
insert into public.account_assessment (
  workspace_id, client_id, as_of, run_kind, replay_run_id, assessment_version, rule_version,
  risk_state, previous_risk_state, state_since, expansion_state, strength, drivers, caveats,
  data, play)
values (
  %(workspace_id)s, %(client_id)s, %(as_of)s, 'replay', %(run_id)s, %(assessment_version)s,
  %(rule_version)s, %(risk_state)s::public.risk_state,
  %(previous_risk_state)s::public.risk_state, %(state_since)s,
  %(expansion_state)s::public.expansion_state, %(strength)s::public.evidence_strength,
  %(drivers)s, %(caveats)s, %(data)s, %(play)s)
returning id
"""

_ADD_EVIDENCE_LINK = """
insert into public.assessment_evidence (workspace_id, assessment_id, evidence_id, client_id)
values (%(workspace_id)s, %(assessment_id)s, %(evidence_id)s, %(client_id)s)
on conflict (assessment_id, evidence_id) do nothing
"""

_FINISH = """
update public.replay_run
   set status = 'succeeded', summary = %(summary)s, finished_at = now(), updated_at = now()
 where workspace_id = %(workspace_id)s and id = %(run_id)s
"""

_FAIL = """
update public.replay_run
   set status = 'failed', error_class = %(error_class)s, finished_at = now(), updated_at = now()
 where workspace_id = %(workspace_id)s and id = %(run_id)s
"""

_CHURN_OUTCOMES = """
select distinct on (client_id) client_id, occurred_at, notice_at, first_known_at
  from public.outcome
 where workspace_id = %(workspace_id)s and event = 'churned'
 order by client_id, occurred_at desc
"""

_CHURNED_AT = """
select id, churned_at from public.client
 where workspace_id = %(workspace_id)s and status = 'churned'
"""


class ReplayRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def create_run(
        self, *, workspace_id: WorkspaceId, window_start: date, window_end: date, step_days: int
    ) -> UUID:
        """Local CLI only; users request runs through the audited `request_replay_run` RPC."""
        with self._pool.connection() as conn:
            row = conn.execute(
                _CREATE,
                {
                    "workspace_id": workspace_id,
                    "window_start": window_start,
                    "window_end": window_end,
                    "step_days": step_days,
                },
            ).fetchone()
        if row is None:  # pragma: no cover - insert returning always yields one row
            raise RuntimeError("replay_run insert returned no id")
        run_id: UUID = row["id"]
        return run_id

    def start(
        self,
        *,
        workspace_id: WorkspaceId,
        run_id: UUID,
        assessment_version: str,
        rule_version: str,
        job_id: UUID | None,
    ) -> dict[str, Any] | None:
        """Marks the run running and deletes rows of an earlier attempt; None if unknown."""
        params = {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "assessment_version": assessment_version,
            "rule_version": rule_version,
            "job_id": job_id,
        }
        with self._pool.connection() as conn:
            row = conn.execute(_START, params).fetchone()
            if row is not None:
                conn.execute(_CLEAR, params)
                conn.execute(_CLEAR_NARRATIVES, params)
        return dict(row) if row else None

    def write_client(
        self,
        *,
        workspace_id: WorkspaceId,
        run_id: UUID,
        client_id: UUID,
        series: list[Assessment],
        assessment_version: str,
        rule_version: str,
    ) -> int:
        """One client's series in one transaction; nothing when the client is now excluded."""
        base = {"workspace_id": workspace_id, "client_id": client_id}
        with self._pool.connection() as conn:
            client = conn.execute(_LOCK_CLIENT, base).fetchone()
            if client is None or client["policy"] == "excluded":
                return 0
            for assessment in series:
                if client["policy"] == "metadata_only" and assessment.evidence_ids():
                    continue  # the policy changed during the run; quotes are gone
                row = conn.execute(
                    _INSERT,
                    {
                        **base,
                        "run_id": run_id,
                        "as_of": assessment.as_of,
                        "assessment_version": assessment_version,
                        "rule_version": rule_version,
                        "risk_state": assessment.risk_state,
                        "previous_risk_state": assessment.previous_risk_state,
                        "state_since": assessment.state_since,
                        "expansion_state": assessment.expansion_state,
                        "strength": assessment.strength,
                        "drivers": Jsonb([d.as_json() for d in assessment.drivers]),
                        "caveats": Jsonb([c.as_json() for c in assessment.caveats]),
                        "data": Jsonb(assessment.data),
                        "play": assessment.play,
                    },
                ).fetchone()
                if row is None:  # pragma: no cover - insert returning always yields one row
                    raise RuntimeError("replay assessment insert returned no id")
                for evidence_id in assessment.evidence_ids():
                    conn.execute(
                        _ADD_EVIDENCE_LINK,
                        {**base, "assessment_id": row["id"], "evidence_id": evidence_id},
                    )
        return len(series)

    def finish(self, *, workspace_id: WorkspaceId, run_id: UUID, summary: dict[str, Any]) -> None:
        with self._pool.connection() as conn:
            self._narrate(conn, workspace_id=workspace_id, run_id=run_id)
            conn.execute(
                _FINISH, {"workspace_id": workspace_id, "run_id": run_id, "summary": Jsonb(summary)}
            )

    def narrate(self, *, workspace_id: WorkspaceId, run_id: UUID) -> bool:
        """Backfill only succeeded runs, atomically replacing narrative rows."""
        with self._pool.connection() as conn:
            return self._narrate(conn, workspace_id=workspace_id, run_id=run_id, backfill=True)

    def _narrate(
        self,
        conn: Connection[DictRow],
        *,
        workspace_id: WorkspaceId,
        run_id: UUID,
        backfill: bool = False,
    ) -> bool:
        params = {"workspace_id": workspace_id, "run_id": run_id}
        # Lock clients before reading derived data, through narrative/summary commit.
        clients = conn.execute(_NARRATIVE_CLIENTS, params).fetchall()
        run = conn.execute(_RUN_FOR_UPDATE, params).fetchone()
        if run is None or (backfill and run["status"] != "succeeded"):
            return False
        rows = conn.execute(_RUN_ROWS, params).fetchall()
        churn = {r["client_id"]: r for r in conn.execute(_CHURN_OUTCOMES, params)}
        for client in clients:
            metadata = client["policy"] == "metadata_only"
            series = [
                StepRow(
                    as_of=r["as_of"],
                    risk=r["risk"],
                    expansion=r["expansion"],
                    play=r["play"],
                    strength=r["strength"],
                    drivers=tuple(
                        d for d in r["drivers"] if not metadata or d.get("basis") == "metadata"
                    ),
                    caveats=tuple(c["code"] for c in r["caveats"]),
                )
                for r in rows
                if r["client_id"] == client["id"]
            ]
            outcome = churn.get(client["id"], {})
            narrative = build_narrative(
                series,
                client_name="",
                status=client["status"],
                churned_at=outcome.get("occurred_at", client["churned_at"]),
                notice_at=outcome.get("notice_at"),
                first_known_at=outcome.get("first_known_at"),
            )
            conn.execute(
                _UPSERT_NARRATIVE,
                {
                    **params,
                    "client_id": client["id"],
                    "narrative": Jsonb(narrative_to_json(narrative)),
                },
            )
        return True

    def fail(self, *, workspace_id: WorkspaceId, run_id: UUID, error_class: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                _FAIL,
                {"workspace_id": workspace_id, "run_id": run_id, "error_class": error_class},
            )

    def churn_facts(
        self, *, workspace_id: WorkspaceId
    ) -> dict[UUID, tuple[date | None, date | None, date | None]]:
        """Per churned client: (churned_at, notice_at, first_known_at); the churn outcome wins
        over `client.churned_at` when the firm recorded one."""
        params = {"workspace_id": workspace_id}
        with self._pool.connection() as conn:
            churned = {
                r["id"]: r["churned_at"] for r in conn.execute(_CHURNED_AT, params).fetchall()
            }
            outcomes = {r["client_id"]: r for r in conn.execute(_CHURN_OUTCOMES, params)}
        result: dict[UUID, tuple[date | None, date | None, date | None]] = {}
        for client_id, churned_at in churned.items():
            outcome = outcomes.get(client_id)
            at = outcome["occurred_at"] if outcome else churned_at
            result[client_id] = (
                at,
                outcome["notice_at"] if outcome else None,
                outcome["first_known_at"] if outcome else None,
            )
        return result


_RUN = """
select id, window_start, window_end, status::text as status, summary
  from public.replay_run where workspace_id = %(workspace_id)s and id = %(run_id)s
"""
_RUN_FOR_UPDATE = _RUN + " for update"

_RUN_ROWS = """
select a.client_id, c.name, c.status::text as status, c.policy::text as policy, a.as_of,
       a.risk_state::text as risk, a.expansion_state::text as expansion, a.play,
       a.strength::text as strength, a.drivers, a.caveats
  from public.account_assessment a
  join public.client c on c.workspace_id = a.workspace_id and c.id = a.client_id
 where a.workspace_id = %(workspace_id)s and a.run_kind = 'replay'
   and a.replay_run_id = %(run_id)s and c.policy <> 'excluded'
 order by c.name, a.as_of
"""

_EVIDENCE = """
select e.id, e.quote, e.deep_link, r.occurred_at,
       r.id as record_id, r.metadata->>'subject' as subject,
       case when p.id is null then null when p.side = 'client' then p.display_name
            else 'our team' end as author_name,
       case when p.side = 'client' then p.role_title end as author_role,
       left(r.id::text, 8) as source_ref
  from public.evidence e
  join public.source_record r on r.workspace_id = e.workspace_id and r.id = e.source_record_id
  left join public.person p on p.workspace_id = r.workspace_id and p.id = r.author_person_id
  join public.client c on c.workspace_id = r.workspace_id and c.id = r.client_id
 where c.policy = 'full' and e.workspace_id = %(workspace_id)s and e.id = any(%(ids)s::uuid[])
"""

_PILOT_RUN = """
select status::text as status, summary->'totals' as totals
  from public.replay_run where workspace_id = %(workspace_id)s and id = %(run_id)s
"""

_PILOT_CLIENTS = """
select c.id as client_id, c.status::text as status, s->>'miss_reason' as miss_reason,
       count(a.id) filter (where a.caveats @> '[{"code":"low_data"}]'::jsonb) as low_data,
       count(a.id) filter (where a.caveats @> '[{"code":"coverage_changed"}]'::jsonb)
         as coverage_changed
  from public.replay_run r
  cross join lateral jsonb_array_elements(coalesce(r.summary->'clients', '[]'::jsonb)) s
  join public.client c on c.workspace_id = r.workspace_id and c.id::text = s->>'client_id'
  left join public.account_assessment a on a.workspace_id = r.workspace_id
       and a.replay_run_id = r.id and a.client_id = c.id and a.run_kind = 'replay'
 where r.workspace_id = %(workspace_id)s and r.id = %(run_id)s and c.policy <> 'excluded'
 group by c.id, c.status, s->>'miss_reason'
 order by c.id
"""


class ReportRepo:
    """Reads a finished replay run for the report; workspace-scoped, read-only."""

    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def run(self, *, workspace_id: WorkspaceId, run_id: UUID) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(_RUN, {"workspace_id": workspace_id, "run_id": run_id}).fetchone()
        return dict(row) if row else None

    def pilot_run(self, *, workspace_id: WorkspaceId, run_id: UUID) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(_PILOT_RUN, {"workspace_id": workspace_id, "run_id": run_id})
            found = row.fetchone()
        return dict(found) if found else None

    def pilot_clients(self, *, workspace_id: WorkspaceId, run_id: UUID) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(_PILOT_CLIENTS, {"workspace_id": workspace_id, "run_id": run_id})
            return [dict(row) for row in rows.fetchall()]

    def rows(self, *, workspace_id: WorkspaceId, run_id: UUID) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    _RUN_ROWS, {"workspace_id": workspace_id, "run_id": run_id}
                ).fetchall()
            ]

    def narratives(self, *, workspace_id: WorkspaceId, run_id: UUID) -> dict[UUID, dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(_NARRATIVES, {"workspace_id": workspace_id, "run_id": run_id})
            return {r["client_id"]: r["narrative"] for r in rows}

    def evidence(self, *, workspace_id: WorkspaceId, ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
        if not ids:
            return {}
        with self._pool.connection() as conn:
            rows = conn.execute(_EVIDENCE, {"workspace_id": workspace_id, "ids": ids}).fetchall()
        out: dict[UUID, dict[str, Any]] = {}
        for r in rows:
            subject = read_field(self._pool, workspace_id, r["record_id"], "subject", r["subject"])
            item = {k: v for k, v in r.items() if k != "record_id"}
            item["subject"] = subject.strip()[:120] if subject else None
            out[r["id"]] = item
        return out


_CLEAR_NARRATIVES = """
delete from public.replay_narrative
 where workspace_id = %(workspace_id)s and replay_run_id = %(run_id)s
"""
_NARRATIVE_CLIENTS = """
select id, status::text as status, policy::text as policy, churned_at
 from public.client where workspace_id = %(workspace_id)s
 and status in ('active', 'churned') and policy <> 'excluded' order by id for share
"""
_UPSERT_NARRATIVE = """
insert into public.replay_narrative (workspace_id, replay_run_id, client_id, version, narrative)
values (%(workspace_id)s, %(run_id)s, %(client_id)s, 1, %(narrative)s)
on conflict (replay_run_id, client_id) do update
set version = excluded.version, narrative = excluded.narrative, updated_at = now()
"""
_NARRATIVES = """
select n.client_id, n.narrative from public.replay_narrative n
join public.client c on c.workspace_id = n.workspace_id and c.id = n.client_id
where n.workspace_id = %(workspace_id)s and n.replay_run_id = %(run_id)s
and n.version = 1 and c.policy <> 'excluded'
"""
