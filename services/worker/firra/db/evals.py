"""Workspace-scoped reads for an already processed local Northbeam workspace."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.cards.checks import check_card
from firra.cards.schema import CardText, Fact
from firra.db.jobs import PgJobStore
from firra.db.locality import is_local_database
from firra.db.types import WorkspaceId
from firra.evals.models import (
    AssessmentObs,
    CardObs,
    EvalInputs,
    EvidenceObs,
    ItemObs,
    NeedObs,
    SignalObs,
    UsageObs,
)
from firra_contracts import TaskEnvelope

UNKNOWN_CLIENT = "unknown_client"
_CARD_FIELDS = {"title", "interpretation", "recommendation"}


def _violation_class(problem: str) -> str:
    """``number:title:45`` -> ``number:title``; anything else -> its class only.

    A violation can carry model-provided text (an unknown fact id), so only a controlled class and
    one of the three card field names ever reach an artifact.
    """
    kind, _, rest = problem.partition(":")
    field = rest.partition(":")[0]
    return f"{kind}:{field}" if field in _CARD_FIELDS else kind


class EvalRepo:
    def __init__(
        self, pool: ConnectionPool[Connection[DictRow]], workspace_id: WorkspaceId
    ) -> None:
        self._pool = pool
        self._workspace_id = workspace_id

    def collect(self) -> EvalInputs:
        """Return observations, never rows from another workspace."""
        params = {"workspace_id": self._workspace_id}
        with self._pool.connection() as conn:
            evidence_rows = conn.execute(
                """
                select e.id, r.external_id, e.quote
                  from public.evidence e
                  join public.source_record r on r.workspace_id = e.workspace_id
                   and r.id = e.source_record_id
                 where e.workspace_id = %(workspace_id)s
                 order by r.external_id, e.id
                """,
                params,
            ).fetchall()
            item_rows = conn.execute(
                """
                select distinct on (x.id) r.external_id, x.kind::text as kind,
                       e.quote, x.payload->>'subtype' as subtype,
                       x.payload->>'budget_direction' as budget_direction,
                       x.payload->>'initiative_type' as initiative_type
                  from public.extraction x
                  join public.extraction_evidence xe on xe.workspace_id = x.workspace_id
                   and xe.extraction_id = x.id
                  join public.evidence e on e.workspace_id = xe.workspace_id
                   and e.id = xe.evidence_id
                  join public.source_record r on r.workspace_id = x.workspace_id
                   and r.id = x.source_record_id
                 where x.workspace_id = %(workspace_id)s
                 order by x.id, e.id
                """,
                params,
            ).fetchall()
            usage_rows = conn.execute(
                """
                select model, input_tokens, output_tokens, cost_usd
                  from public.llm_usage
                 where workspace_id = %(workspace_id)s
                 order by model, id
                """,
                params,
            ).fetchall()
            job_rows = conn.execute(
                """
                select progress from public.job
                 where workspace_id = %(workspace_id)s and type = 'process.records'
                   and status in ('succeeded', 'failed')
                 order by id
                """,
                params,
            ).fetchall()
        accepted = 0
        dropped = 0
        verify_dropped = 0
        for row in job_rows:
            progress = row["progress"]
            accepted += int(progress.get("items", 0))
            reasons = progress.get("dropped", {})
            dropped += sum(int(n) for n in reasons.values())
            verify_dropped += sum(
                int(n) for reason, n in reasons.items() if reason.startswith("quote_")
            )
        return EvalInputs(
            evidence=[
                EvidenceObs(
                    evidence_id=UUID(str(row["id"])),
                    external_id=str(row["external_id"]),
                    quote=str(row["quote"]),
                )
                for row in evidence_rows
            ],
            items=[ItemObs(**row) for row in item_rows],
            usage=[UsageObs(**row) for row in usage_rows],
            verify_dropped=verify_dropped,
            items_attempted=accepted + dropped,
        )

    def collect_signals(self, run_id: UUID, client_keys: Mapping[str, str]) -> list[SignalObs]:
        """Signals Firra raised at any week of the replay, one per client and type.

        The live ``signal`` table only holds what is active at ``as_of``; a warning planted months
        earlier (and every signal of a churned client) is visible only in the replay history, which
        is what the product shows. Evidence ids are the union over the weeks.
        """
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                select c.name as client, d->>'type' as type,
                       array(select distinct e from jsonb_array_elements(
                                jsonb_agg(d->'evidence_ids')) l,
                              jsonb_array_elements_text(l) e) as evidence_ids
                  from public.account_assessment a
                  join public.client c on c.workspace_id = a.workspace_id and c.id = a.client_id
                  cross join lateral jsonb_array_elements(a.drivers) d
                 where a.workspace_id = %(workspace_id)s and a.run_kind = 'replay'
                   and a.replay_run_id = %(run_id)s
                 group by c.name, d->>'type'
                 order by c.name, d->>'type'
                """,
                {"workspace_id": self._workspace_id, "run_id": run_id},
            ).fetchall()
        return [
            SignalObs(
                client=client_keys[row["client"]],
                type=row["type"],
                evidence_ids=[UUID(e) for e in row["evidence_ids"]],
            )
            for row in rows
            if row["client"] in client_keys
        ]

    def collect_needs(
        self, client_keys: Mapping[str, str], service_keys: Mapping[str, str]
    ) -> list[NeedObs]:
        """Open latent needs; ``service`` is the spec key of the matched workspace service."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                select c.name as client, coalesce(n.topic, n.cluster_key) as topic,
                       n.mention_count as mentions, n.thread_count as threads, s.name as service
                  from public.latent_need n
                  join public.client c on c.workspace_id = n.workspace_id and c.id = n.client_id
                  left join public.workspace_service s
                    on s.workspace_id = n.workspace_id and s.id = n.workspace_service_id
                 where n.workspace_id = %(workspace_id)s and n.status = 'open'
                 order by c.name, n.id
                """,
                {"workspace_id": self._workspace_id},
            ).fetchall()
        return [
            NeedObs(
                client=client_keys[row["client"]],
                topic=row["topic"],
                mentions=row["mentions"],
                threads=row["threads"],
                service=service_keys.get(row["service"]) if row["service"] else None,
            )
            for row in rows
            if row["client"] in client_keys
        ]

    def collect_cards(self, client_keys: Mapping[str, str]) -> list[CardObs]:
        """Stored cards, each re-checked against its own stored facts and verified quotes.

        The write path already drops a card that fails ``check_card``; this proves what was
        persisted, not what was attempted. Artifacts keep only violation classes, never text.
        """
        params = {"workspace_id": self._workspace_id}
        with self._pool.connection() as conn:
            cards = conn.execute(
                """
                select c.name as client, d.kind::text as kind, d.title, d.interpretation,
                       d.recommendation, d.facts, d.inputs->'fact_ids' as fact_ids,
                       array(select distinct x->>'type'
                               from jsonb_array_elements(coalesce(a.drivers, '[]'::jsonb)) x
                              order by 1) as types,
                       array(select distinct x->>'type'
                               from jsonb_array_elements(coalesce(a.drivers, '[]'::jsonb)) x
                              where coalesce((x->>'primary')::boolean, false)
                              order by 1) as primary_types
                  from public.decision_card d
                  join public.client c on c.workspace_id = d.workspace_id and c.id = d.client_id
                  left join public.account_assessment a
                    on a.workspace_id = d.workspace_id and a.id = d.assessment_id
                 where d.workspace_id = %(workspace_id)s
                 order by c.name, d.week, d.id
                """,
                params,
            ).fetchall()
            quotes = {
                str(row["id"]): str(row["quote"])
                for row in conn.execute(
                    "select id, quote from public.evidence where workspace_id = %(workspace_id)s",
                    params,
                ).fetchall()
            }
        observed: list[CardObs] = []
        for row in cards:
            facts = [
                Fact(
                    id=f["id"],
                    text=f["text"],
                    basis=f["basis"],
                    evidence_ids=[UUID(e) for e in f.get("evidence_ids", [])],
                    inputs=f.get("inputs"),
                    quotes=[quotes[e] for e in f.get("evidence_ids", []) if e in quotes],
                )
                for f in row["facts"]
            ]
            text = CardText(
                title=row["title"],
                interpretation=row["interpretation"],
                recommendation=row["recommendation"],
                fact_ids=row["fact_ids"] or [],
            )
            problems = check_card(text, facts, allowed_text=row["client"])
            observed.append(
                CardObs(
                    # A card for a client truth does not know stays visible, under a sentinel, so
                    # an incomplete truth map cannot turn into "no false positives".
                    client=client_keys.get(row["client"], UNKNOWN_CLIENT),
                    kind=row["kind"],
                    # What the card rests on: the primary drivers, else everything that fired.
                    source_signal_types=list(row["primary_types"] or row["types"]),
                    integrity_violations=sorted({_violation_class(p) for p in problems}),
                )
            )
        return observed

    def client_names(self) -> dict[UUID, str]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select id, name from public.client where workspace_id = %(workspace_id)s",
                {"workspace_id": self._workspace_id},
            ).fetchall()
        return {UUID(str(row["id"])): str(row["name"]) for row in rows}

    def collect_replay(self, run_id: UUID) -> list[AssessmentObs]:
        """Weekly assessments of one replay run; the client's name is resolved to a key by truth."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                select c.name as client, a.as_of, a.risk_state::text as risk,
                       a.expansion_state::text as expansion, a.play,
                       array(select x->>'code' from jsonb_array_elements(a.caveats) x
                              order by x->>'code') as caveats
                  from public.account_assessment a
                  join public.client c on c.workspace_id = a.workspace_id and c.id = a.client_id
                 where a.workspace_id = %(workspace_id)s and a.run_kind = 'replay'
                   and a.replay_run_id = %(run_id)s
                 order by c.name, a.as_of
                """,
                {"workspace_id": self._workspace_id, "run_id": run_id},
            ).fetchall()
        return [AssessmentObs(**{**row, "caveats": tuple(row["caveats"])}) for row in rows]


@dataclass(frozen=True)
class RunState:
    active: int
    pending: int
    jobs: list[dict[str, Any]]
    failed: list[dict[str, str]]
    usage: dict[str, int | str]


class EvalRunRepo:
    """Local-only orchestration; every read and write belongs to the new eval workspace."""

    def __init__(
        self, pool: ConnectionPool[Connection[DictRow]], workspace_id: WorkspaceId
    ) -> None:
        conninfo = pool.conninfo() if callable(pool.conninfo) else pool.conninfo
        if not is_local_database(conninfo):
            raise ValueError("eval run requires local Postgres")
        self.pool = pool
        self.workspace_id = workspace_id

    def discard_live_assessments_after(self, as_of: date) -> int:
        """Drop live assessments dated after the evaluated day.

        The first processing follow-up rebuilds memory at today's date, which is later than the
        corpus's ``as_of`` whenever the run happens on another day. Cards pick the newest live
        assessment of their week, so they would word that state instead of the one being scored.
        Eval workspaces are synthetic, local and single-use.
        """
        with self.pool.connection() as conn:
            cursor = conn.execute(
                "delete from public.account_assessment where workspace_id = %(workspace_id)s "
                "and run_kind = 'live' and as_of > %(as_of)s",
                {"workspace_id": self.workspace_id, "as_of": as_of},
            )
        return cursor.rowcount

    def set_budget(self, budget_usd: Decimal) -> None:
        if not budget_usd.is_finite() or budget_usd <= 0:
            raise ValueError("eval budget must be positive")
        with self.pool.connection() as conn:
            conn.execute(
                "update public.workspace set llm_budget_usd_month = %(budget)s "
                "where id = %(workspace_id)s",
                {"workspace_id": self.workspace_id, "budget": budget_usd},
            )

    def request_job(self, job_type: str, payload: dict[str, Any]) -> TaskEnvelope:
        key = f"eval:{job_type}"
        job_id = PgJobStore(self.pool).create(
            workspace_id=self.workspace_id,
            job_type=job_type,
            idempotency_key=key,
            payload=payload,
        )
        if job_id is None:
            raise RuntimeError("eval job already requested")
        return TaskEnvelope(
            workspace_id=self.workspace_id,
            job_id=job_id,
            type=job_type,
            idempotency_key=key,
            payload=payload,
        )

    def stalled_jobs(self, *, older_than_seconds: float) -> list[TaskEnvelope]:
        """Queued jobs nobody is working on and nobody will deliver, ready to be delivered again.

        A task can outlast its delivery window; the worker then answers 503 to a client that has
        gone, and the job waits queued (Cloud Tasks would redeliver it, the local dispatcher never
        resends on a timeout). Only while no job is running, and each job is returned once per
        interval (its ``updated_at`` is bumped).
        """
        with self.pool.connection() as conn:
            rows = conn.execute(
                "update public.job j set updated_at = now() "
                "where j.workspace_id = %(workspace_id)s and j.status = 'queued' "
                "and j.updated_at < now() - make_interval(secs => %(secs)s) "
                "and not exists (select 1 from public.job r "
                "where r.workspace_id = j.workspace_id and r.status = 'running') "
                "returning j.id, j.type, j.idempotency_key, j.payload",
                {"workspace_id": self.workspace_id, "secs": older_than_seconds},
            ).fetchall()
        return [
            TaskEnvelope(
                workspace_id=self.workspace_id,
                job_id=row["id"],
                type=row["type"],
                idempotency_key=row["idempotency_key"],
                payload=row["payload"],
            )
            for row in rows
        ]

    def state(self) -> RunState:
        params = {"workspace_id": self.workspace_id}
        with self.pool.connection() as conn:
            jobs = conn.execute(
                "select type, status::text as status, count(*)::int as count from public.job "
                "where workspace_id = %(workspace_id)s group by type, status order by type, status",
                params,
            ).fetchall()
            failed = conn.execute(
                "select type, coalesce(error_class, 'unknown') as error_class from public.job "
                "where workspace_id = %(workspace_id)s and status = 'failed' order by type, id",
                params,
            ).fetchall()
            records = conn.execute(
                "select count(*)::int as pending from public.source_record "
                "where workspace_id = %(workspace_id)s and processing_state = 'pending'",
                params,
            ).fetchone()
            usage = conn.execute(
                "select coalesce(sum(input_tokens), 0)::bigint as input_tokens, "
                "coalesce(sum(output_tokens), 0)::bigint as output_tokens, "
                "coalesce(sum(cost_usd), 0) as cost_usd from public.llm_usage "
                "where workspace_id = %(workspace_id)s",
                params,
            ).fetchone()
        if records is None or usage is None:
            raise RuntimeError("eval counters unavailable")
        return RunState(
            active=sum(r["count"] for r in jobs if r["status"] in {"queued", "running"}),
            pending=records["pending"],
            jobs=[dict(r) for r in jobs],
            failed=[dict(r) for r in failed],
            usage={
                "input_tokens": int(usage["input_tokens"]),
                "output_tokens": int(usage["output_tokens"]),
                "cost_usd": str(usage["cost_usd"]),
            },
        )
