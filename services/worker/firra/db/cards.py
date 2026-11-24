"""Workspace-scoped weekly cards on the existing decision_card schema."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.assessment.model import Assessment, Caveat, Driver
from firra.cards.schema import CardText, Fact
from firra.cards.select import MAX_CARDS, Candidate
from firra.cards.word import PROMPT_VERSION
from firra.db.types import WorkspaceId


def _assessment(row: dict[str, Any]) -> Assessment:
    return Assessment(
        as_of=row["as_of"],
        risk_state=row["risk_state"],
        previous_risk_state=row["previous_risk_state"],
        state_since=row["state_since"],
        expansion_state=row["expansion_state"],
        strength=row["strength"],
        play=row["play"],
        data=row["data"],
        drivers=tuple(
            Driver(
                type=d["type"],
                family=d["family"],
                severity=d["severity"],
                counted_severity=d["counted_severity"],
                basis=d["basis"],
                dedupe_key=d["dedupe_key"],
                occurred_at=datetime.fromisoformat(d["occurred_at"]),
                inputs=d["inputs"],
                evidence_ids=tuple(UUID(e) for e in d["evidence_ids"]),
                primary=d["primary"],
            )
            for d in row["drivers"]
        ),
        caveats=tuple(
            Caveat(
                c["code"], c["detail"], {k: v for k, v in c.items() if k not in {"code", "detail"}}
            )
            for c in row["caveats"]
        ),
    )


class CardRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self.pool = pool

    def candidates(self, *, workspace_id: WorkspaceId, week: date) -> tuple[list[Candidate], int]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                select distinct on (a.client_id) a.*, c.name as client_name, coalesce(c.mrr, 0) as
                    mrr,
                       c.renewal_date, c.notice_period_days,
                       (select start.previous_risk_state from public.account_assessment start
                         where start.workspace_id = a.workspace_id
                           and start.client_id = a.client_id and start.run_kind = 'live'
                           and start.as_of = a.state_since) as state_before_change,
                       (select max(d.week) from public.decision_card d
                         where d.workspace_id = a.workspace_id and d.client_id = a.client_id) as
                             last_card_week
                  from public.account_assessment a join public.client c
                    on c.workspace_id = a.workspace_id and c.id = a.client_id
                 where a.workspace_id = %(workspace_id)s and a.run_kind = 'live'
                   and a.as_of >= %(week)s and a.as_of < %(end)s
                   and c.status = 'active' and c.policy <> 'excluded'
                 order by a.client_id, a.as_of desc
            """,
                {"workspace_id": workspace_id, "week": week, "end": week + timedelta(days=7)},
            ).fetchall()
            count = conn.execute(
                """
                select count(*)::int as n from public.decision_card where workspace_id =
                %(workspace_id)s and week = %(week)s
                """,
                {"workspace_id": workspace_id, "week": week},
            ).fetchone()
        candidates = []
        for row in rows:
            notice = row["renewal_date"]
            if notice is not None:
                notice -= timedelta(days=row["notice_period_days"] or 0)
            candidates.append(
                Candidate(
                    row["client_id"],
                    row["client_name"],
                    row["id"],
                    _assessment(row),
                    Decimal(row["mrr"]),
                    (notice - row["as_of"]).days if notice else None,
                    row["last_card_week"],
                    row["state_before_change"],
                )
            )
        return candidates, count["n"] if count else 0

    @contextmanager
    def prepare(
        self, *, workspace_id: WorkspaceId, week: date, candidate: Candidate
    ) -> Iterator[tuple[Connection[DictRow], dict[UUID, str]] | None]:
        with self.pool.connection() as conn, conn.transaction():
            # Serialize this week's inserts and recheck the limit before paying for wording.
            conn.execute(
                """
                select pg_advisory_xact_lock(hashtextextended(%(workspace_id)s::text ||
                %(week)s::text, 0))
                """,
                {"workspace_id": workspace_id, "week": week},
            )
            client = conn.execute(
                """
                select policy from public.client where workspace_id = %(workspace_id)s and id =
                %(client_id)s and status = 'active' and policy <> 'excluded' for update
                """,
                {"workspace_id": workspace_id, "client_id": candidate.client_id},
            ).fetchone()
            cards = conn.execute(
                """
                select client_id from public.decision_card where workspace_id = %(workspace_id)s and
                week = %(week)s
                """,
                {"workspace_id": workspace_id, "week": week},
            ).fetchall()
            if (
                client is None
                or len(cards) >= MAX_CARDS
                or any(c["client_id"] == candidate.client_id for c in cards)
            ):
                yield None
                return
            evidence = candidate.assessment.evidence_ids()
            if client["policy"] == "metadata_only" and (
                evidence or any(d.basis == "evidence" for d in candidate.assessment.drivers)
            ):
                yield None
                return
            quotes = (
                conn.execute(
                    """
                    select id, quote from public.evidence where workspace_id = %(workspace_id)s and
                    client_id = %(client_id)s and id = any(%(ids)s)
                    """,
                    {
                        "workspace_id": workspace_id,
                        "client_id": candidate.client_id,
                        "ids": list(evidence),
                    },
                ).fetchall()
                if client["policy"] == "full"
                else []
            )
            mapping = {q["id"]: q["quote"] for q in quotes}
            if set(evidence) - mapping.keys():
                yield None
                return
            yield conn, mapping

    def save(
        self,
        conn: Connection[DictRow],
        *,
        workspace_id: WorkspaceId,
        week: date,
        candidate: Candidate,
        facts: Sequence[Fact],
        card: CardText,
        body: str,
        priority: int,
        model: str,
        prompt_version: str = PROMPT_VERSION,
    ) -> None:
        conn.execute(
            """
            insert into public.decision_card (workspace_id, client_id, week, kind, title, body,
                locale,
                score, priority, inputs, model, prompt_version, dedupe_key, assessment_id, play,
                facts, interpretation, recommendation, strength, caveats)
            values (%(workspace_id)s, %(client_id)s, %(week)s, %(kind)s, %(title)s, %(body)s, 'en',
                %(score)s, %(priority)s, %(inputs)s, %(model)s, %(prompt)s, %(dedupe)s,
                    %(assessment_id)s,
                %(play)s, %(facts)s, %(interpretation)s, %(recommendation)s, %(strength)s,
                    %(caveats)s)
            returning id
        """,
            {
                "workspace_id": workspace_id,
                "client_id": candidate.client_id,
                "week": week,
                "kind": "growth"
                if candidate.assessment.play == "expansion_conversation"
                else "risk",
                "title": card.title,
                "body": body,
                "score": MAX_CARDS + 1 - priority,
                "priority": priority,
                "inputs": Jsonb(
                    {"assessment_id": str(candidate.assessment_id), "fact_ids": card.fact_ids}
                ),
                "model": model,
                "prompt": prompt_version,
                "dedupe": str(candidate.client_id),
                "assessment_id": candidate.assessment_id,
                "play": candidate.assessment.play,
                "facts": Jsonb([f.stored() for f in facts]),
                "interpretation": card.interpretation,
                "recommendation": card.recommendation,
                "strength": candidate.assessment.strength,
                "caveats": Jsonb([c.as_json() for c in candidate.assessment.caveats]),
            },
        ).fetchone()
        conn.execute(
            """
            insert into public.decision_evidence (workspace_id, client_id, decision_card_id,
                evidence_id)
            select %(workspace_id)s, %(client_id)s, d.id, e.id
              from public.decision_card d join public.evidence e
                on e.workspace_id = d.workspace_id and e.client_id = d.client_id
             where d.workspace_id = %(workspace_id)s and d.client_id = %(client_id)s
               and d.week = %(week)s and d.dedupe_key = %(dedupe)s and e.id = any(%(ids)s)
        """,
            {
                "workspace_id": workspace_id,
                "client_id": candidate.client_id,
                "week": week,
                "dedupe": str(candidate.client_id),
                "ids": list(dict.fromkeys(e for f in facts for e in f.evidence_ids)),
            },
        )
