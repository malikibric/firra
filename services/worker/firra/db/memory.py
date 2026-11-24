"""Workspace-scoped account memory repository. Each client rebuild is one transaction."""

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId
from firra.memory.rules import (
    ChampionCandidate,
    MemoryConfig,
    card_changed,
    choose_champion,
    commitment_status,
    dedupe_key,
    has_stated_budget,
    is_decision_maker,
    normalized,
    stakeholder_change_supported,
)
from firra.memory.unknowns import CardFacts, unknowns

_SELECT_ROWS = {
    "commitment": "select id, dedupe_key, status::text as status from public.commitment "
    "where workspace_id = %(workspace_id)s and client_id = %(client_id)s",
    "client_goal": "select id, dedupe_key from public.client_goal "
    "where workspace_id = %(workspace_id)s and client_id = %(client_id)s",
    "request": "select id, dedupe_key from public.request "
    "where workspace_id = %(workspace_id)s and client_id = %(client_id)s",
}

_DELETE_ROW = {
    "commitment": "delete from public.commitment where workspace_id = %(workspace_id)s "
    "and client_id = %(client_id)s and id = %(id)s",
    "client_goal": "delete from public.client_goal where workspace_id = %(workspace_id)s "
    "and client_id = %(client_id)s and id = %(id)s",
    "request": "delete from public.request where workspace_id = %(workspace_id)s "
    "and client_id = %(client_id)s and id = %(id)s",
}

_DELETE_STALE_EVIDENCE = {
    "commitment": "delete from public.commitment_evidence "
    "where workspace_id = %(workspace_id)s and client_id = %(client_id)s "
    "and commitment_id = %(parent_id)s and evidence_id <> all(%(evidence_ids)s::uuid[])",
    "client_goal": "delete from public.client_goal_evidence "
    "where workspace_id = %(workspace_id)s and client_id = %(client_id)s "
    "and client_goal_id = %(parent_id)s and evidence_id <> all(%(evidence_ids)s::uuid[])",
    "request": "delete from public.request_evidence "
    "where workspace_id = %(workspace_id)s and client_id = %(client_id)s "
    "and request_id = %(parent_id)s and evidence_id <> all(%(evidence_ids)s::uuid[])",
}

_INSERT_EVIDENCE = {
    "commitment": "insert into public.commitment_evidence "
    "(workspace_id, client_id, commitment_id, evidence_id) "
    "values (%(workspace_id)s, %(client_id)s, %(parent_id)s, %(evidence_id)s) "
    "on conflict (commitment_id, evidence_id) do nothing",
    "client_goal": "insert into public.client_goal_evidence "
    "(workspace_id, client_id, client_goal_id, evidence_id) "
    "values (%(workspace_id)s, %(client_id)s, %(parent_id)s, %(evidence_id)s) "
    "on conflict (client_goal_id, evidence_id) do nothing",
    "request": "insert into public.request_evidence "
    "(workspace_id, client_id, request_id, evidence_id) "
    "values (%(workspace_id)s, %(client_id)s, %(parent_id)s, %(evidence_id)s) "
    "on conflict (request_id, evidence_id) do nothing",
}


class MemoryRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def clients(self, *, workspace_id: WorkspaceId) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            return conn.execute(
                "select id, policy::text from public.client "
                "where workspace_id = %(workspace_id)s and policy <> 'excluded' order by id",
                {"workspace_id": workspace_id},
            ).fetchall()

    def rebuild_client(
        self,
        *,
        workspace_id: WorkspaceId,
        client_id: UUID,
        as_of: date,
        cfg: MemoryConfig,
    ) -> bool:
        """Rebuild current memory; a past as_of is for evals, never a historical replay."""
        base = {"workspace_id": workspace_id, "client_id": client_id}
        cutoff = datetime.combine(as_of + timedelta(days=1), time.min, UTC)
        with self._pool.connection() as conn:
            # Serialize concurrent rebuilds and policy changes for this client.
            row = conn.execute(
                "select policy::text, renewal_date from public.client "
                "where workspace_id = %(workspace_id)s "
                "and id = %(client_id)s for update",
                base,
            ).fetchone()
            if row is None or row["policy"] == "excluded":
                return False
            policy = row["policy"]
            items = (
                []
                if policy == "metadata_only"
                else conn.execute(
                    """select x.kind::text, x.payload, x.source_record_id, r.occurred_at,
                          r.thread_key, r.author_person_id, e.id as evidence_id, e.quote
                     from public.extraction x
                     join public.extraction_evidence xe on xe.workspace_id = x.workspace_id
                       and xe.extraction_id = x.id and xe.client_id = x.client_id
                     join public.evidence e on e.workspace_id = xe.workspace_id
                       and e.id = xe.evidence_id and e.client_id = xe.client_id
                     join public.source_record r on r.workspace_id = x.workspace_id
                       and r.id = x.source_record_id and r.client_id = x.client_id
                    where x.workspace_id = %(workspace_id)s and x.client_id = %(client_id)s
                      and r.occurred_at < %(end)s
                    order by r.occurred_at, x.id, e.id""",
                    {**base, "end": cutoff},
                ).fetchall()
            )
            persons = conn.execute(
                """select id, display_name, role_title, is_decision_maker, is_champion,
                          departed_at, first_seen_at from public.person
                    where workspace_id = %(workspace_id)s and client_id = %(client_id)s
                      and side = 'client' and merged_into_person_id is null
                      and (first_seen_at is null or first_seen_at < %(end)s)
                    order by first_seen_at nulls last, id""",
                {**base, "end": cutoff},
            ).fetchall()
            by_name = {normalized(p["display_name"]): p for p in persons if p["display_name"]}
            observations: list[dict[str, Any]] = []
            for item in items:
                if item["kind"] != "stakeholder_change":
                    continue
                payload = item["payload"]
                name = payload.get("person_name")
                if not name:
                    continue
                person = by_name.get(normalized(name))
                title = (
                    payload.get("role_title")
                    if payload.get("change") in {"joined", "new_role"}
                    else None
                )
                if not stakeholder_change_supported(
                    person_name=name,
                    role_title=title,
                    quote=item["quote"],
                    change=payload.get("change", ""),
                    author_person_id=item["author_person_id"],
                    matched_person_id=person["id"] if person else None,
                ):
                    continue
                if person is None and payload.get("change") in {"joined", "new_role"}:
                    person = conn.execute(
                        """insert into public.person (workspace_id, client_id, side, display_name,
                              first_seen_at) values (%(workspace_id)s, %(client_id)s, 'client',
                              %(name)s, %(at)s) returning id, display_name, role_title,
                              is_decision_maker, is_champion, departed_at, first_seen_at""",
                        {**base, "name": name, "at": item["occurred_at"]},
                    ).fetchone()
                    if person is not None:
                        by_name[normalized(name)] = person
                if person is None:
                    continue
                if payload.get("change") == "left":
                    conn.execute(
                        "update public.person set departed_at = %(at)s where workspace_id = "
                        "%(workspace_id)s and client_id = %(client_id)s and id = %(id)s "
                        "and departed_at is distinct from %(at)s",
                        {**base, "id": person["id"], "at": item["occurred_at"]},
                    )
                if payload.get("change") in {"joined", "new_role"} and title:
                    conn.execute(
                        """update public.person set role_title = %(title)s,
                              is_decision_maker = is_decision_maker or %(decision)s
                            where workspace_id = %(workspace_id)s and client_id = %(client_id)s
                              and id = %(id)s and
                              (role_title is distinct from %(title)s or
                               (not is_decision_maker and %(decision)s))""",
                        {
                            **base,
                            "id": person["id"],
                            "title": title[:200],
                            "decision": is_decision_maker(title, cfg),
                        },
                    )
                    observations.append(
                        {
                            "person_id": person["id"],
                            "observed_at": item["occurred_at"],
                            "title": title[:120],
                            "decision": is_decision_maker(title, cfg),
                            "record_id": item["source_record_id"],
                        }
                    )
            self._sync_observations(conn, base, observations, cutoff)

            start = cutoff - timedelta(days=cfg.champion_window_days)
            counts = conn.execute(
                """select author_person_id as id, count(*)::int as message_count
                     from public.source_record
                    where workspace_id = %(workspace_id)s and client_id = %(client_id)s
                      and author_person_id is not null and direction = 'in'
                      and occurred_at >= %(start)s and occurred_at < %(end)s
                    group by author_person_id""",
                {**base, "start": start, "end": cutoff},
            ).fetchall()
            count_by_id = {r["id"]: r["message_count"] for r in counts}
            persons = conn.execute(
                """select id, display_name, role_title, is_decision_maker, is_champion,
                          departed_at, first_seen_at from public.person
                    where workspace_id = %(workspace_id)s and client_id = %(client_id)s
                      and side = 'client' and merged_into_person_id is null
                      and (first_seen_at is null or first_seen_at < %(end)s)
                    order by first_seen_at nulls last, id""",
                {**base, "end": cutoff},
            ).fetchall()
            champion = choose_champion(
                [
                    ChampionCandidate(p["id"], count_by_id.get(p["id"], 0), p["first_seen_at"])
                    for p in persons
                ],
                cfg,
            )
            conn.execute(
                """update public.person set is_champion = (id is not distinct from %(champion)s)
                    where workspace_id = %(workspace_id)s and client_id = %(client_id)s
                      and side = 'client' and is_champion is distinct from
                        (id is not distinct from %(champion)s)""",
                {**base, "champion": champion},
            )
            for person in persons:
                person["is_champion"] = person["id"] == champion

            desired: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
            results_by_quote: dict[str, dict[str, Any]] = {}
            for item in items:
                kind = item["kind"]
                payload = item["payload"]
                if kind == "result_reported":
                    result_key = dedupe_key(None, item["quote"])
                    if result_key not in results_by_quote:
                        results_by_quote[result_key] = {
                            "text": payload["text"],
                            "metric": payload.get("metric"),
                            "occurred_at": item["occurred_at"].isoformat(),
                            "evidence_ids": set(),
                        }
                    results_by_quote[result_key]["evidence_ids"].add(str(item["evidence_id"]))
                    continue
                table = {
                    "commitment": "commitment",
                    "goal": "client_goal",
                    "request": "request",
                    "complaint": "request",
                }.get(kind)
                if table is None:
                    continue
                key = dedupe_key(
                    None if table == "request" else item["source_record_id"], item["quote"]
                )
                entry = desired[table].get(key)
                if entry is None:
                    desired[table][key] = {
                        "key": key,
                        "text": payload["text"][:500],
                        "owner_side": payload.get("owner_side"),
                        "due_date": payload.get("due_date"),
                        "metric": payload.get("metric"),
                        "origin_kind": kind,
                        "source_record_id": item["source_record_id"],
                        "thread_key": item["thread_key"],
                        "occurred_at": item["occurred_at"],
                        "evidence_ids": {item["evidence_id"]},
                    }
                else:
                    entry["evidence_ids"].add(item["evidence_id"])

            card_rows: dict[str, list[dict[str, Any]]] = {}
            for table in ("commitment", "client_goal", "request"):
                card_rows[table] = (
                    self._sync_rows(
                        conn,
                        base,
                        table,
                        desired[table],
                        as_of,
                    )
                    if policy == "full"
                    else []
                )
            # metadata_only has no body-derived rows; a later policy change is handled by T13.

            timeline = conn.execute(
                """select min(occurred_at) as first_contact_at,
                          max(occurred_at) filter (where direction = 'in')
                            as last_client_message_at,
                          max(occurred_at) filter (where direction = 'in' and
                            author_person_id = %(champion)s) as last_champion_message_at
                     from public.source_record
                    where workspace_id = %(workspace_id)s and client_id = %(client_id)s
                      and occurred_at < %(end)s""",
                {**base, "champion": champion, "end": cutoff},
            ).fetchone()
            if timeline is None:  # pragma: no cover - aggregate query always returns one row
                raise RuntimeError("timeline aggregate returned no row")
            stakeholders = [
                {
                    "person_id": str(p["id"]),
                    "name": p["display_name"],
                    "title": p["role_title"],
                    "is_decision_maker": p["is_decision_maker"],
                    "is_champion": p["is_champion"],
                    "departed_at": p["departed_at"].isoformat() if p["departed_at"] else None,
                }
                for p in persons
            ]
            latest_result = max(
                (i["occurred_at"] for i in items if i["kind"] == "result_reported"), default=None
            )
            facts = CardFacts(
                has_goal=bool(card_rows["client_goal"]),
                has_decision_maker=any(p["is_decision_maker"] for p in persons),
                renewal_date=row["renewal_date"],
                last_result_at=latest_result,
                champion_id=str(champion) if champion else None,
                last_champion_message_at=timeline["last_champion_message_at"],
                budget_known=any(
                    item["kind"] == "renewal_or_budget_talk" and has_stated_budget(item["quote"])
                    for item in items
                ),
            )
            content: dict[str, Any] = {
                "version": cfg.version,
                "as_of": as_of.isoformat(),
                "stakeholders": stakeholders,
                "timeline": {
                    key: value.isoformat() if value else None for key, value in timeline.items()
                },
                "unknowns": unknowns(facts, as_of=as_of, cfg=cfg),
            }
            if policy == "full":
                open_commitments = [
                    item for item in card_rows["commitment"] if item["status"] in {"open", "late"}
                ]
                content.update(
                    {
                        "commitments": {
                            "client": [r for r in open_commitments if r["owner_side"] == "client"],
                            "firm": [r for r in open_commitments if r["owner_side"] == "firm"],
                        },
                        "goals": card_rows["client_goal"],
                        "results": [
                            {**result, "evidence_ids": sorted(result["evidence_ids"])}
                            for result in results_by_quote.values()
                        ],
                        "requests": card_rows["request"],
                    }
                )
            previous = conn.execute(
                """select version, content from public.client_card
                    where workspace_id = %(workspace_id)s and client_id = %(client_id)s
                    order by version desc limit 1""",
                base,
            ).fetchone()
            if not card_changed(previous["content"] if previous else None, content):
                return False
            conn.execute(
                """insert into public.client_card (workspace_id, client_id, version, content)
                    values (%(workspace_id)s, %(client_id)s, %(version)s, %(content)s)""",
                {
                    **base,
                    "version": previous["version"] + 1 if previous else 1,
                    "content": Jsonb(content),
                },
            )
            return True

    @staticmethod
    def _sync_observations(
        conn: Connection[DictRow],
        base: dict[str, Any],
        observations: list[dict[str, Any]],
        cutoff: datetime,
    ) -> None:
        """Dated role observations from stakeholder changes (P2b D5), in step with the items."""
        conn.execute(
            """delete from public.person_observation
                where workspace_id = %(workspace_id)s and client_id = %(client_id)s
                  and source = 'stakeholder_change' and observed_at < %(end)s
                  and (person_id, observed_at) not in (
                    select * from unnest(%(person_ids)s::uuid[], %(observed)s::timestamptz[]))""",
            {
                **base,
                "end": cutoff,
                "person_ids": [o["person_id"] for o in observations],
                "observed": [o["observed_at"] for o in observations],
            },
        )
        for observation in observations:
            conn.execute(
                """insert into public.person_observation (workspace_id, client_id, person_id,
                      observed_at, source, role_title, is_decision_maker, source_record_id)
                    values (%(workspace_id)s, %(client_id)s, %(person_id)s, %(observed_at)s,
                      'stakeholder_change', %(title)s, %(decision)s, %(record_id)s)
                    on conflict (workspace_id, person_id, source, observed_at) do update
                      set role_title = excluded.role_title,
                          is_decision_maker = excluded.is_decision_maker,
                          source_record_id = excluded.source_record_id""",
                {**base, **observation},
            )

    def _sync_rows(
        self,
        conn: Connection[DictRow],
        base: dict[str, Any],
        table: str,
        wanted: dict[str, dict[str, Any]],
        as_of: date,
    ) -> list[dict[str, Any]]:
        existing = conn.execute(_SELECT_ROWS[table], base).fetchall()
        for row in existing:
            if row["dedupe_key"] not in wanted and (
                table != "commitment" or row["status"] not in {"done", "cancelled"}
            ):
                conn.execute(_DELETE_ROW[table], {**base, "id": row["id"]})
        rows: list[dict[str, Any]] = []
        for entry in wanted.values():
            params = {**base, **entry}
            if table == "commitment":
                params["status"] = commitment_status(
                    "open",
                    date.fromisoformat(entry["due_date"]) if entry["due_date"] else None,
                    as_of,
                )
                result = conn.execute(
                    """insert into public.commitment (workspace_id, client_id, owner_side, text,
                          due_date, status, stated_at, dedupe_key)
                        values (%(workspace_id)s, %(client_id)s, %(owner_side)s, %(text)s,
                          %(due_date)s, %(status)s, %(occurred_at)s, %(key)s)
                        on conflict (workspace_id, client_id, dedupe_key) do update
                          set status = case when commitment.status in ('done', 'cancelled')
                            then commitment.status else excluded.status end,
                              text = excluded.text, due_date = excluded.due_date
                        returning id, status::text""",
                    params,
                ).fetchone()
            elif table == "client_goal":
                result = conn.execute(
                    """insert into public.client_goal (workspace_id, client_id, text, metric,
                          stated_at, dedupe_key)
                        values (%(workspace_id)s, %(client_id)s, %(text)s, %(metric)s,
                          %(occurred_at)s, %(key)s)
                        on conflict (workspace_id, client_id, dedupe_key) do update
                          set text = excluded.text, metric = excluded.metric returning id""",
                    params,
                ).fetchone()
            else:
                result = conn.execute(
                    """insert into public.request (workspace_id, client_id, origin_kind, text,
                          source_record_id, thread_key, occurred_at, dedupe_key)
                        values (%(workspace_id)s, %(client_id)s, %(origin_kind)s, %(text)s,
                          %(source_record_id)s, %(thread_key)s, %(occurred_at)s, %(key)s)
                        on conflict (workspace_id, client_id, dedupe_key) do update
                          set source_record_id = excluded.source_record_id,
                              occurred_at = excluded.occurred_at,
                              thread_key = excluded.thread_key, text = excluded.text,
                              embedding = case
                                when public.request.text is distinct from excluded.text
                                then null else public.request.embedding end,
                              embedding_model = case
                                when public.request.text is distinct from excluded.text
                                then null else public.request.embedding_model end
                        returning id""",
                    params,
                ).fetchone()
            if result is None:  # pragma: no cover - insert returning always yields one row
                raise RuntimeError("memory insert returned no row")
            conn.execute(
                _DELETE_STALE_EVIDENCE[table],
                {**base, "parent_id": result["id"], "evidence_ids": list(entry["evidence_ids"])},
            )
            for evidence_id in entry["evidence_ids"]:
                conn.execute(
                    _INSERT_EVIDENCE[table],
                    {**base, "parent_id": result["id"], "evidence_id": evidence_id},
                )
            rows.append(
                {
                    "id": str(result["id"]),
                    "text": entry["text"],
                    "evidence_ids": sorted(str(e) for e in entry["evidence_ids"]),
                    **(
                        {
                            "owner_side": entry["owner_side"],
                            "due_date": entry["due_date"],
                            "status": result["status"],
                        }
                        if table == "commitment"
                        else {}
                    ),
                    **({"metric": entry["metric"]} if table == "client_goal" else {}),
                    **(
                        {
                            "origin_kind": entry["origin_kind"],
                            "occurred_at": entry["occurred_at"].isoformat(),
                        }
                        if table == "request"
                        else {}
                    ),
                }
            )
        return rows
