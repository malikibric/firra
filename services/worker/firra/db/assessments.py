"""Account assessment storage (P2b D6, D8). Every statement is scoped by workspace_id.

Live assessments are one row per client and ``as_of`` day (``run_kind = live``); a re-run on the
same day replaces the row and its links. The writer runs inside the caller's per-client
transaction (``SignalRepo.save``), so signals, their history and the assessment land together.
"""

from datetime import date
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.assessment.assess import previous_from_row
from firra.assessment.model import Assessment, PreviousAssessment
from firra.db.types import WorkspaceId

_PREVIOUS_LIVE = """
select as_of, risk_state::text as risk_state, state_since, data
  from public.account_assessment
 where workspace_id = %(workspace_id)s and client_id = %(client_id)s and run_kind = 'live'
   and as_of < %(as_of)s
 order by as_of desc
 limit 1
"""

_UPSERT_LIVE = """
insert into public.account_assessment (
  workspace_id, client_id, as_of, run_kind, replay_run_id, assessment_version, rule_version,
  risk_state, previous_risk_state, state_since, expansion_state, strength, drivers, caveats,
  data, play)
values (
  %(workspace_id)s, %(client_id)s, %(as_of)s, 'live', null, %(assessment_version)s,
  %(rule_version)s, %(risk_state)s::public.risk_state,
  %(previous_risk_state)s::public.risk_state, %(state_since)s,
  %(expansion_state)s::public.expansion_state, %(strength)s::public.evidence_strength,
  %(drivers)s, %(caveats)s, %(data)s, %(play)s)
on conflict (workspace_id, client_id, run_kind, replay_run_id, as_of) do update
   set assessment_version = excluded.assessment_version, rule_version = excluded.rule_version,
       risk_state = excluded.risk_state, previous_risk_state = excluded.previous_risk_state,
       state_since = excluded.state_since, expansion_state = excluded.expansion_state,
       strength = excluded.strength, drivers = excluded.drivers, caveats = excluded.caveats,
       data = excluded.data, play = excluded.play, updated_at = now()
returning id
"""

_DROP_SIGNAL_LINKS = """
delete from public.assessment_signal
 where workspace_id = %(workspace_id)s and client_id = %(client_id)s
   and assessment_id = %(assessment_id)s
"""

_ADD_SIGNAL_LINK = """
insert into public.assessment_signal (workspace_id, assessment_id, signal_id, client_id)
values (%(workspace_id)s, %(assessment_id)s, %(signal_id)s, %(client_id)s)
on conflict (assessment_id, signal_id) do nothing
"""

_DROP_EVIDENCE_LINKS = """
delete from public.assessment_evidence
 where workspace_id = %(workspace_id)s and client_id = %(client_id)s
   and assessment_id = %(assessment_id)s
"""

_ADD_EVIDENCE_LINK = """
insert into public.assessment_evidence (workspace_id, assessment_id, evidence_id, client_id)
values (%(workspace_id)s, %(assessment_id)s, %(evidence_id)s, %(client_id)s)
on conflict (assessment_id, evidence_id) do nothing
"""


class AssessmentRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def previous_live(
        self, *, workspace_id: WorkspaceId, client_id: UUID, as_of: date
    ) -> PreviousAssessment | None:
        """The latest live assessment strictly before ``as_of`` (same-day re-runs are stable)."""
        with self._pool.connection() as conn:
            row = conn.execute(
                _PREVIOUS_LIVE,
                {"workspace_id": workspace_id, "client_id": client_id, "as_of": as_of},
            ).fetchone()
        return previous_from_row(row) if row else None


def write_live(
    conn: Connection[DictRow],
    base: dict[str, Any],
    assessment: Assessment,
    *,
    signal_ids: dict[tuple[str, str], UUID],
    assessment_version: str,
    rule_version: str,
) -> UUID:
    """Upserts the day's live assessment and replaces its signal and evidence links."""
    row = conn.execute(
        _UPSERT_LIVE,
        {
            **base,
            "as_of": assessment.as_of,
            "assessment_version": assessment_version,
            "rule_version": rule_version,
            "risk_state": assessment.risk_state,
            "previous_risk_state": assessment.previous_risk_state,
            "state_since": assessment.state_since,
            "expansion_state": assessment.expansion_state,
            "strength": assessment.strength,
            "drivers": Jsonb(
                [d.as_json(signal_ids.get((d.type, d.dedupe_key))) for d in assessment.drivers]
            ),
            "caveats": Jsonb([c.as_json() for c in assessment.caveats]),
            "data": Jsonb(assessment.data),
            "play": assessment.play,
        },
    ).fetchone()
    if row is None:  # pragma: no cover - insert returning always yields one row
        raise RuntimeError("assessment upsert returned no id")
    assessment_id: UUID = row["id"]
    link = {**base, "assessment_id": assessment_id}
    conn.execute(_DROP_SIGNAL_LINKS, link)
    for signal_id in dict.fromkeys(
        signal_ids[(d.type, d.dedupe_key)]
        for d in assessment.drivers
        if (d.type, d.dedupe_key) in signal_ids
    ):
        conn.execute(_ADD_SIGNAL_LINK, {**link, "signal_id": signal_id})
    conn.execute(_DROP_EVIDENCE_LINKS, link)
    for evidence_id in assessment.evidence_ids():
        conn.execute(_ADD_EVIDENCE_LINK, {**link, "evidence_id": evidence_id})
    return assessment_id
