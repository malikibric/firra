"""MCP source layer: client-scoped search, bounded context, refresh and sync status (S3).

Every call: owner switch on, one resolvable non-excluded client, audited before the answer, text of
third parties only as bounded ``untrusted_text``. Reads the synced local database; the only action
is asking the normal sync to run (throttled). See ``docs/plans/local/source-layer.md``.
"""

import hashlib
import time
from datetime import datetime
from typing import Any
from uuid import UUID

from firra.db import local_clients as clients
from firra.db import local_mcp, local_sources
from firra.db.types import WorkspaceId
from firra.local.mcp.request_scope import REQUEST_SCOPES
from firra.local.mcp.tools_read import UnavailableError
from firra.local.readctx import ReadContext
from firra.local.settings import load
from firra.logging import get_logger
from firra.sources.search import (
    CONTEXT_CHARS,
    SUBJECT_CHARS,
    Candidate,
    clean_text,
    fit_budget,
    query_terms,
    rank,
)
from firra_contracts import TaskEnvelope
from firra_contracts.generated.local.source_context import SourceContextResult
from firra_contracts.generated.local.source_refresh import SourceRefreshResult
from firra_contracts.generated.local.source_search import SourceSearchResult
from firra_contracts.generated.local.sync_status import SyncStatusResult

log = get_logger(__name__)
SOURCE_TOOLS = (
    "search_client_sources",
    "get_source_context",
    "refresh_client_sources",
    "get_sync_status",
)
SWITCH_OFF = "Source access is off. The owner can switch it on in Firra Settings."
NOT_PERMITTED = "This connector link does not include source access."
NOT_FOUND = "Source not found."
THROTTLE_SECONDS = 120
LABEL = {"email": "email", "chat": "slack", "calendar_event": "calendar"}
SOURCE_OF = {"email": "email", "slack": "slack", "calendar": "calendar"}


class SourceError(Exception):
    """A safe, fixed message for the AI (the only texts that may leave this layer)."""


def _untrusted(text: str | None, limit: int) -> dict[str, str] | None:
    cleaned = clean_text(text or "")[:limit]
    return {"untrusted_text": cleaned} if cleaned else None


def _ref(record_id: UUID) -> str:
    return "src_" + record_id.hex


def _thread_ref(row: dict[str, Any]) -> str | None:
    key = "|".join(str(row.get(k) or "") for k in ("channel_id", "thread_key"))
    return hashlib.sha256(key.encode()).hexdigest()[:16] if key != "|" else None


def _author(row: dict[str, Any]) -> dict[str, Any]:
    name = clean_text(row["author_name"] or "")[:200] or None
    return {"name": name, "side": row["side"] if row["side"] in ("client", "firm") else "unknown"}


class SourceTools:
    def __init__(
        self, ctx: ReadContext, *, enqueue: Any = None, sleep: Any = time.sleep, clock: Any = None
    ) -> None:
        self.ctx = ctx
        self.enqueue = enqueue
        self.sleep = sleep
        self.monotonic = clock or time.monotonic
        self.ws = WorkspaceId(ctx.identity.workspace_id)
        self._last_refresh: dict[UUID, float] = {}

    # -- shared steps ---------------------------------------------------------------------

    def _switch(self) -> None:
        scopes = REQUEST_SCOPES.get()
        if scopes is not None and "sources" not in scopes:
            raise SourceError(NOT_PERMITTED)
        if not load(self.ctx.home).mcp_source_access:
            raise SourceError(SWITCH_OFF)

    def _client(self, name: str) -> dict[str, Any]:
        with self.ctx.caller() as conn:
            row = local_sources.resolve_client(conn, self.ws, name)
        if row is None:
            raise UnavailableError()
        return dict(row)

    def _audit(self, tool: str, count: int, client_id: UUID | None, **extra: Any) -> None:
        with self.ctx.pool.connection() as conn:
            local_mcp.audit(
                conn,
                workspace_id=self.ws,
                actor_id=self.ctx.identity.user_id,
                name=tool,
                count=count,
                target_id=client_id,
                extra=extra or None,
            )

    # -- tools ----------------------------------------------------------------------------

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self._switch()
        return getattr(self, name)(args)  # type: ignore[no-any-return]

    def search_client_sources(self, args: dict[str, Any]) -> dict[str, Any]:
        client = self._client(str(args["client"]))
        sources = list(args.get("sources") or ["email", "slack", "calendar"])
        limit = int(args.get("limit") or 8)
        terms = query_terms(str(args["query"]))
        full = client["policy"] == "full"
        first, last, total = local_sources.oldest_and_count(
            self.ctx.pool, self.ws, client["id"], sources, content_only=full
        )
        hits: list[dict[str, Any]] = []
        if full and terms:
            rows = local_sources.records(
                self.ctx.pool,
                self.ws,
                client["id"],
                sources,
                args.get("since"),
                args.get("until"),
            )
            by_key = {str(r["id"]): r for r in rows}
            candidates = [
                Candidate(str(r["id"]), r["occurred_at"], r["text"] or "", r["subject"])
                for r in rows
            ]
            ranked = rank(candidates, terms, limit)
            sizes = [
                len(m.excerpt) + len(clean_text(by_key[c.key]["subject"] or "")[:SUBJECT_CHARS])
                for c, m in ranked
            ]
            for candidate, found in ranked[: fit_budget(sizes)]:
                row = by_key[candidate.key]
                hits.append(
                    {
                        "source_ref": _ref(row["id"]),
                        "source": LABEL[row["kind"]],
                        "occurred_at": row["occurred_at"],
                        "author": _author(row),
                        "thread_ref": _thread_ref(row),
                        "subject": _untrusted(row["subject"], SUBJECT_CHARS),
                        "excerpt": _untrusted(found.excerpt, 280),
                        "permalink": row["permalink"],
                        "has_evidence": row["has_evidence"],
                    }
                )
        self._audit(
            "search_client_sources", len(hits), client["id"], sources=sources, matched=bool(hits)
        )
        return SourceSearchResult.model_validate(
            {
                "kind": "raw_source",
                "client_id": client["id"],
                "searched": {
                    "sources": sources,
                    "from": first,
                    "to": last,
                    "records": total,
                    "content_stored": full,
                },
                "hits": hits,
            }
        ).model_dump(mode="json", by_alias=True)

    def get_source_context(self, args: dict[str, Any]) -> dict[str, Any]:
        found = local_sources.record_of_ref(self.ctx.pool, self.ws, str(args["ref"]))
        if found is None:
            raise SourceError(NOT_FOUND)
        record_id, client_id = found
        with self.ctx.caller() as conn:
            visible = clients.client(conn, self.ws, client_id)
        if visible is None or visible["policy"] != "full":
            raise SourceError(NOT_FOUND)
        rows = local_sources.neighbours(
            self.ctx.pool,
            self.ws,
            client_id,
            record_id,
            int(args.get("before", 2)),
            int(args.get("after", 2)),
        )
        if not rows:
            raise SourceError(NOT_FOUND)
        sizes = [len(clean_text(r["text"] or "")[:CONTEXT_CHARS]) for r in rows]
        kept = rows[: fit_budget(sizes)] if sizes[0] <= 8000 else rows[:1]
        messages = [
            {
                "source_ref": _ref(r["id"]),
                "source": LABEL[r["kind"]],
                "occurred_at": r["occurred_at"],
                "author": _author(r),
                "is_target": r["id"] == record_id,
                "subject": _untrusted(r["subject"], SUBJECT_CHARS),
                "text": _untrusted(r["text"], CONTEXT_CHARS),
                "permalink": r["permalink"],
            }
            for r in kept
        ]
        self._audit("get_source_context", len(messages), client_id)
        return SourceContextResult.model_validate(
            {
                "kind": "raw_source",
                "client_id": client_id,
                "messages": messages,
                "truncated": len(kept) < len(rows),
            }
        ).model_dump(mode="json")

    def refresh_client_sources(self, args: dict[str, Any]) -> dict[str, Any]:
        client = self._client(str(args["client"]))
        wait = min(max(int(args.get("wait_seconds", 10)), 0), 20)
        connections = local_sources.carrying_connections(self.ctx.pool, self.ws, client["id"])
        usable = [c for c in connections if c["provider"] in ("gmail", "slack")]
        result: dict[str, Any] = {
            "state": "no_connection",
            "connections": 0,
            "new_records": 0,
            "synced_until": None,
        }
        if usable:
            result = self._refresh(client["id"], usable, wait)
        new_records = int(result["new_records"])
        self._audit("refresh_client_sources", new_records, client["id"], state=result["state"])
        return SourceRefreshResult.model_validate(result).model_dump(mode="json")

    def _settled(self, job_ids: list[UUID]) -> tuple[bool, list[str]]:
        statuses = local_sources.job_statuses(self.ctx.pool, self.ws, job_ids)
        return all(s in ("succeeded", "failed") for s in statuses), statuses

    def _refresh(self, client_id: UUID, usable: list[Any], wait: int) -> dict[str, Any]:
        live = [c for c in usable if c["status"] == "active"]
        now = self.monotonic()
        fresh = [c for c in live if now - self._last_refresh.get(c["id"], -1e9) >= THROTTLE_SECONDS]
        base: dict[str, Any] = {
            "connections": len(live),
            "new_records": 0,
            "synced_until": _latest(live),
        }
        if not live:
            return {**base, "state": "needs_reconnect"}
        if not fresh:
            return {**base, "state": "throttled"}
        if self.enqueue is None:
            raise UnavailableError()
        before = local_sources.record_count(self.ctx.pool, self.ws, client_id)
        job_ids = [self._enqueue_sync(c) for c in fresh]
        for c in fresh:
            self._last_refresh[c["id"]] = now
        deadline = self.monotonic() + wait
        settled, statuses = self._settled(job_ids)
        while not settled and self.monotonic() < deadline:
            self.sleep(0.5)
            settled, statuses = self._settled(job_ids)
        added = max(0, local_sources.record_count(self.ctx.pool, self.ws, client_id) - before)
        refreshed = local_sources.carrying_connections(self.ctx.pool, self.ws, client_id)
        if not settled:
            state = "running"
        elif all(s == "succeeded" for s in statuses) and len(live) == len(usable):
            state = "done"
        else:
            state = "needs_reconnect"
        return {
            "state": state,
            "connections": len(live),
            "new_records": added,
            "synced_until": _latest(refreshed),
        }

    def _enqueue_sync(self, connection: Any) -> UUID:
        from firra.local.routes.onboarding import _envelope

        provider_task = "sync.slack" if connection["provider"] == "slack" else "sync.incremental"
        payload = {"connection_id": str(connection["id"])}
        key = f"refresh:{connection['id']}:{int(self.monotonic() * 1000)}:{time.time_ns()}"
        envelope: TaskEnvelope = _envelope(self.ctx, connection["id"], provider_task, key, payload)
        self.enqueue(envelope)
        return envelope.job_id

    def get_sync_status(self, args: dict[str, Any]) -> dict[str, Any]:
        client_id: UUID | None = None
        if args.get("client"):
            client_id = self._client(str(args["client"]))["id"]
        rows = local_sources.carrying_connections(self.ctx.pool, self.ws, client_id)
        items = [
            {
                "provider": r["provider"],
                "status": r["status"],
                "last_synced_at": r["last_synced_at"],
                "action": _action(r),
            }
            for r in rows
            if r["provider"] in ("gmail", "google_calendar", "slack", "email_upload")
        ]
        waiting = local_sources.waiting_for_ai(self.ctx.pool, self.ws, client_id)
        self._audit("get_sync_status", len(items), client_id)
        return SyncStatusResult.model_validate(
            {"connections": items, "waiting_for_ai": waiting}
        ).model_dump(mode="json")


def _latest(rows: list[Any]) -> datetime | None:
    stamps = [r["last_synced_at"] for r in rows if r["last_synced_at"] is not None]
    return max(stamps) if stamps else None


def _action(row: Any) -> str | None:
    if row["status"] in ("revoked", "error"):
        return "reconnect_slack" if row["provider"] == "slack" else "reconnect_google"
    return "wait" if row["last_synced_at"] is None else None
