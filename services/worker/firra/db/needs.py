"""Workspace-scoped latent-demand reads and atomic per-client writes."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.types import JobId, WorkspaceId
from firra.needs.cluster import Request


@dataclass(frozen=True, slots=True)
class PendingRequest:
    id: UUID
    text: str


@dataclass(frozen=True, slots=True)
class NeedDraft:
    cluster_key: str
    request_ids: tuple[UUID, ...]
    service_id: UUID | None
    first_seen_at: datetime
    last_seen_at: datetime


class NeedsRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self._pool = pool

    def request_job(
        self, *, workspace_id: WorkspaceId, key: str, payload: Mapping[str, object]
    ) -> JobId | None:
        """Serialize scheduling so there is at most one queued or running job per workspace."""
        with self._pool.connection() as conn:
            conn.execute(
                "select pg_advisory_xact_lock(hashtextextended(%(workspace_id)s::text, 0))",
                {"workspace_id": workspace_id},
            )
            row = conn.execute(
                "insert into public.job (workspace_id, type, idempotency_key, payload) "
                "select %(workspace_id)s, 'needs.update', %(key)s, %(payload)s "
                "where not exists (select 1 from public.job where workspace_id = %(workspace_id)s "
                "and type = 'needs.update' and status in ('queued', 'running')) "
                "on conflict (workspace_id, idempotency_key) do nothing returning id",
                {"workspace_id": workspace_id, "key": key, "payload": Jsonb(dict(payload))},
            ).fetchone()
        return None if row is None else JobId(row["id"])

    def clients(self, *, workspace_id: WorkspaceId) -> list[UUID]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select id from public.client where workspace_id = %(workspace_id)s "
                "and policy = 'full' order by id",
                {"workspace_id": workspace_id},
            ).fetchall()
        return [row["id"] for row in rows]

    def services(self, *, workspace_id: WorkspaceId) -> list[tuple[UUID, str]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select id, name, description from public.workspace_service "
                "where workspace_id = %(workspace_id)s order by id",
                {"workspace_id": workspace_id},
            ).fetchall()
        return [(row["id"], row["name"] + ". " + row["description"]) for row in rows]

    def requests(
        self, *, workspace_id: WorkspaceId, client_id: UUID, as_of: datetime
    ) -> list[tuple[Request | PendingRequest, str, datetime, str | None]]:
        """Rows include the original metadata while unembedded requests await model calls."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select r.id, r.dedupe_key, r.text, r.occurred_at, r.thread_key, "
                "r.embedding::real[] as embedding "
                "from public.request r join public.client c "
                "on c.workspace_id = r.workspace_id and c.id = r.client_id "
                "where r.workspace_id = %(workspace_id)s and r.client_id = %(client_id)s "
                "and c.policy = 'full' and r.occurred_at <= %(as_of)s "
                "order by r.occurred_at, r.id",
                {"workspace_id": workspace_id, "client_id": client_id, "as_of": as_of},
            ).fetchall()
        result: list[tuple[Request | PendingRequest, str, datetime, str | None]] = []
        for row in rows:
            if row["embedding"] is None:
                item: Request | PendingRequest = PendingRequest(row["id"], row["text"])
            else:
                values = tuple(float(value) for value in row["embedding"])
                item = Request(
                    row["id"],
                    row["dedupe_key"],
                    row["text"],
                    row["occurred_at"],
                    row["thread_key"],
                    values,
                )
            result.append((item, row["dedupe_key"], row["occurred_at"], row["thread_key"]))
        return result

    def save_client(
        self,
        *,
        workspace_id: WorkspaceId,
        client_id: UUID,
        embedded: list[tuple[UUID, str, tuple[float, ...]]],
        drafts: list[NeedDraft],
        model: str,
        version: str,
        min_mentions: int,
        min_threads: int,
    ) -> int:
        base = {"workspace_id": workspace_id, "client_id": client_id}
        written = 0
        produced: list[str] = []
        with self._pool.connection() as conn:
            client = conn.execute(
                "select policy::text as policy from public.client "
                "where workspace_id = %(workspace_id)s and id = %(client_id)s for update",
                base,
            ).fetchone()
            if client is None or client["policy"] != "full":
                return 0
            for request_id, request_text, vector in embedded:
                updated = conn.execute(
                    "update public.request set embedding = %(embedding)s::real[], "
                    "embedding_model = %(model)s where workspace_id = %(workspace_id)s "
                    "and client_id = %(client_id)s and id = %(request_id)s "
                    "and text = %(text)s and embedding is null returning id",
                    {
                        **base,
                        "request_id": request_id,
                        "text": request_text,
                        "embedding": list(vector),
                        "model": model,
                    },
                ).fetchone()
                if updated is None:
                    raise RuntimeError("request changed during embedding")
            for draft in drafts:
                need = conn.execute(
                    "insert into public.latent_need (workspace_id, client_id, cluster_key, "
                    "workspace_service_id, mention_count, thread_count, first_seen_at, "
                    "last_seen_at, rule_version) values (%(workspace_id)s, %(client_id)s, "
                    "%(cluster_key)s, %(service_id)s, %(min_mentions)s, %(min_threads)s, "
                    "%(first_seen_at)s, %(last_seen_at)s, %(version)s) "
                    "on conflict (workspace_id, client_id, cluster_key) do update set "
                    "workspace_service_id = excluded.workspace_service_id, "
                    "first_seen_at = excluded.first_seen_at, last_seen_at = excluded.last_seen_at, "
                    "rule_version = excluded.rule_version where public.latent_need.status = 'open' "
                    "returning id",
                    {
                        **base,
                        "cluster_key": draft.cluster_key,
                        "service_id": draft.service_id,
                        "min_mentions": min_mentions,
                        "min_threads": min_threads,
                        "first_seen_at": draft.first_seen_at,
                        "last_seen_at": draft.last_seen_at,
                        "version": version,
                    },
                ).fetchone()
                if need is None:
                    continue  # addressed and dismissed needs stay untouched
                need_id = need["id"]
                produced.append(draft.cluster_key)
                conn.execute(
                    "delete from public.latent_need_request where workspace_id = %(workspace_id)s "
                    "and client_id = %(client_id)s and latent_need_id = %(need_id)s",
                    {**base, "need_id": need_id},
                )
                conn.execute(
                    "insert into public.latent_need_request "
                    "(workspace_id, client_id, latent_need_id, request_id) "
                    "select %(workspace_id)s, %(client_id)s, %(need_id)s, r.id "
                    "from public.request r where r.workspace_id = %(workspace_id)s "
                    "and r.client_id = %(client_id)s and r.id = any(%(request_ids)s::uuid[])",
                    {**base, "need_id": need_id, "request_ids": list(draft.request_ids)},
                )
                conn.execute(
                    "delete from public.latent_need_evidence where workspace_id = %(workspace_id)s "
                    "and client_id = %(client_id)s and latent_need_id = %(need_id)s",
                    {**base, "need_id": need_id},
                )
                conn.execute(
                    "insert into public.latent_need_evidence "
                    "(workspace_id, client_id, latent_need_id, evidence_id) "
                    "select distinct %(workspace_id)s, %(client_id)s, %(need_id)s, re.evidence_id "
                    "from public.latent_need_request nr join public.request_evidence re "
                    "on re.workspace_id = nr.workspace_id and re.client_id = nr.client_id "
                    "and re.request_id = nr.request_id where nr.workspace_id = %(workspace_id)s "
                    "and nr.client_id = %(client_id)s and nr.latent_need_id = %(need_id)s",
                    {**base, "need_id": need_id},
                )
                counts = conn.execute(
                    "update public.latent_need n set (mention_count, thread_count) = "
                    "(select count(*)::int, count(distinct r.thread_key)::int "
                    "from public.latent_need_request nr join public.request r "
                    "on r.workspace_id = nr.workspace_id and r.client_id = nr.client_id "
                    "and r.id = nr.request_id where nr.workspace_id = %(workspace_id)s "
                    "and nr.client_id = %(client_id)s and nr.latent_need_id = %(need_id)s) "
                    "where n.workspace_id = %(workspace_id)s and n.client_id = %(client_id)s "
                    "and n.id = %(need_id)s returning mention_count, thread_count",
                    {**base, "need_id": need_id},
                ).fetchone()
                if (
                    counts is None
                    or counts["mention_count"] < min_mentions
                    or counts["thread_count"] < min_threads
                ):
                    raise RuntimeError("latent need SQL counts do not qualify")
                written += 1
            conn.execute(
                "delete from public.latent_need where workspace_id = %(workspace_id)s "
                "and client_id = %(client_id)s and status = 'open' "
                "and not (cluster_key = any(%(produced)s::text[]))",
                {**base, "produced": produced},
            )
        return written
