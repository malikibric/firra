"""MCP adapters over the same repositories and builders as the local API."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from firra.db import local_clients as clients
from firra.db import local_decisions as decisions
from firra.db import local_evidence as evidence
from firra.db import local_mcp
from firra.db.types import WorkspaceId
from firra.local.paging import instant, page, parse_page
from firra.local.readctx import ReadContext
from firra.local.routes.client_builders import (
    build_attention,
    build_changes,
    build_client_detail,
    build_client_list_item,
)
from firra.local.routes.evidence_builders import shape_evidence, shape_replay, shape_run
from firra_contracts.generated_v1.attention_response import AttentionResponse
from firra_contracts.generated_v1.changes_response import ChangesResponse
from firra_contracts.generated_v1.client_detail_response import ClientDetailResponse
from firra_contracts.generated_v1.clients_response import ClientsResponse
from firra_contracts.generated_v1.decisions_response import DecisionsResponse
from firra_contracts.generated_v1.mcp_client_change import McpClientChange
from firra_contracts.generated_v1.mcp_commitments import McpCommitments
from firra_contracts.generated_v1.mcp_evidence import McpEvidence
from firra_contracts.generated_v1.mcp_replay import McpReplay
from firra_contracts.generated_v1.mcp_stakeholders import McpStakeholders
from firra_contracts.generated_v1.mcp_unknowns import McpUnknowns
from firra_contracts.generated_v1.timeline_response import TimelineResponse

RESULT_MODELS: dict[str, type[BaseModel]] = {
    "get_clients_needing_attention": AttentionResponse,
    "list_clients": ClientsResponse,
    "get_recent_changes": ChangesResponse,
    "get_client_state": ClientDetailResponse,
    "get_client_context": ClientDetailResponse,
    "get_risks": McpClientChange,
    "get_commitments": McpCommitments,
    "get_stakeholders": McpStakeholders,
    "get_client_unknowns": McpUnknowns,
    "get_client_timeline": TimelineResponse,
    "search_evidence": McpEvidence,
    "get_churn_replay": McpReplay,
    "get_decisions": DecisionsResponse,
}


class UnavailableError(Exception):
    """An unavailable or policy-hidden stored read model."""


def read(ctx: ReadContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    ws = WorkspaceId(ctx.identity.workspace_id)
    now = ctx.clock()
    client_id = UUID(str(args["client_id"])) if args.get("client_id") else None
    body: BaseModel
    with ctx.caller() as conn:
        if name == "get_clients_needing_attention":
            body = AttentionResponse.model_validate(
                dict(
                    generated_at=now,
                    items=build_attention(
                        clients.attention(conn, ws, now), clients.visible_clients(conn, ws), now
                    ),
                )
            )
        elif name == "list_clients":
            rows = clients.clients(conn, ws, "active")
            body = ClientsResponse.model_validate(
                dict(
                    generated_at=now,
                    items=[build_client_list_item(row, row["assessment"]) for row in rows],
                )
            )
        elif name == "get_recent_changes":
            since = (now - timedelta(days=args.get("days", 7))).date().isoformat()
            body = ChangesResponse.model_validate(
                dict(
                    generated_at=now,
                    since=since,
                    items=build_changes(
                        clients.changes_assessments(conn, ws, since, now),
                        clients.changes_events(conn, ws, since, now),
                        clients.visible_clients(conn, ws),
                        since,
                        now,
                    ),
                )
            )
        elif name in (
            "get_client_state",
            "get_client_context",
            "get_risks",
            "get_commitments",
            "get_stakeholders",
            "get_client_unknowns",
        ):
            if client_id is None:
                raise UnavailableError()
            row = clients.client(conn, ws, client_id)
            if row is None:
                raise UnavailableError()
            assessment, history = clients.detail_assessments(conn, ws, client_id)
            parts = clients.detail_parts(
                conn, ws, client_id, metadata_only=row["policy"] == "metadata_only"
            )
            detail = ClientDetailResponse.model_validate(
                dict(generated_at=now, client=build_client_detail(row, assessment, history, parts))
            )
            value = detail.model_dump(mode="json", by_alias=True)["client"]
            if name in ("get_client_state", "get_client_context"):
                body = detail
            elif name == "get_risks":
                body = McpClientChange.model_validate(
                    dict(
                        generated_at=now,
                        client_id=client_id,
                        state_history=value["state_history"],
                        assessment=value["assessment"],
                    )
                )
            else:
                models: dict[str, tuple[type[BaseModel], str]] = {
                    "get_commitments": (McpCommitments, "commitments"),
                    "get_stakeholders": (McpStakeholders, "stakeholders"),
                    "get_client_unknowns": (McpUnknowns, "unknowns"),
                }
                model, field = models[name]
                body = model.model_validate(
                    dict(generated_at=now, client_id=client_id, items=value.get(field) or [])
                )
        elif name in ("get_client_timeline", "search_evidence"):
            query = parse_page(
                str(args.get("limit", 50)), args.get("cursor"), evidence=name == "search_evidence"
            )
            if query is None or client_id is None:
                raise ValueError()
            client = evidence.visible(conn, ws, client_id)
            if client is None:
                raise UnavailableError()
            if name == "get_client_timeline":
                rows = evidence.timeline(
                    conn,
                    ws,
                    client_id,
                    query,
                    now,
                    metadata_only=client["policy"] == "metadata_only",
                )
                items, cursor = page(rows, query)
                body = TimelineResponse.model_validate(dict(items=items, next_cursor=cursor))
            else:
                rows = (
                    evidence.client_evidence(conn, ws, client_id, query, now)
                    if client["policy"] == "full"
                    else []
                )
                items, cursor = page(
                    [shape_evidence(row, pool=ctx.pool, workspace_id=ws) for row in rows], query
                )
                for item in items:
                    item["untrusted_quote"] = item.pop("quote")
                body = McpEvidence.model_validate(dict(items=items, next_cursor=cursor))
        elif name == "prepare_for_client":
            from firra.local.mcp.tools_prepare import prepare

            if client_id is None:
                raise ValueError()
            body = prepare(ctx, client_id, args.get("meeting_at"))
        elif name == "get_opportunities":
            from firra.local.mcp.tools_models import GrowthResult

            if client_id is not None and evidence.visible(conn, ws, client_id) is None:
                raise UnavailableError()
            expansions = []
            for row in clients.clients(conn, ws, "active"):
                if client_id is not None and row["id"] != client_id:
                    continue
                assessment = row["assessment"]
                if not assessment or assessment["expansion_state"] == "none":
                    continue
                drivers = assessment.get("drivers") or []
                ids = (
                    sorted(
                        {
                            str(e)
                            for d in drivers
                            if d.get("basis") == "evidence"
                            for e in d.get("evidence_ids", [])
                        }
                    )
                    if row["policy"] == "full"
                    else []
                )
                expansions.append(
                    dict(
                        fact_id=assessment["id"],
                        client_id=row["id"],
                        expansion_state=assessment["expansion_state"],
                        as_of=assessment["as_of"],
                        evidence_ids=ids,
                    )
                )
            needs = local_mcp.growth(conn, workspace_id=ws, client_id=client_id)
            body = GrowthResult.model_validate(
                dict(generated_at=now, expansions=expansions, latent_needs=needs)
            )
        elif name == "get_evidence":
            from firra.local.mcp.tools_models import EvidenceResult
            from firra.local.settings import load

            opted_in = load(ctx.home).mcp_source_access
            evidence_id = UUID(str(args["evidence_id"]))
            if args.get("with_context") and not opted_in:
                raise UnavailableError()
            row = evidence.evidence(conn, ws, evidence_id)
            if row is None:
                raise UnavailableError()
            value = shape_evidence(row, pool=ctx.pool, workspace_id=ws)
            value["untrusted_quote"] = value.pop("quote")
            context = (
                local_mcp.evidence_context(
                    conn,
                    workspace_id=ws,
                    evidence_id=evidence_id,
                    user_id=ctx.identity.user_id,
                    pool=ctx.pool,
                )
                if args.get("with_context")
                else None
            )
            if args.get("with_context") and context is None:
                raise UnavailableError()
            body = EvidenceResult.model_validate(
                {**value, **({"untrusted_context": context} if context is not None else {})}
            )
        elif name == "get_churn_replay":
            run_id = args.get("run_id")
            if bool(run_id) != bool(client_id):
                raise ValueError()
            if not run_id:
                body = McpReplay.model_validate(
                    dict(
                        runs=dict(
                            generated_at=now,
                            items=[shape_run(row) for row in evidence.replay_runs(conn, ws)],
                        )
                    )
                )
            else:
                run = UUID(str(run_id))
                if client_id is None:
                    raise ValueError()
                client = evidence.visible(conn, ws, client_id)
                row = evidence.replay_narrative(conn, ws, run, client_id)
                if client is None or row is None:
                    raise UnavailableError()
                value = shape_replay(
                    row["narrative"],
                    [],
                    policy=client["policy"],
                    run_id=run,
                    client_id=client_id,
                    now=now,
                )
                if value is None:
                    raise UnavailableError()
                value["weeks"] = evidence.replay_weeks(conn, ws, run, client_id)
                current = evidence.visible(conn, ws, client_id)
                if current is None or current["policy"] != client["policy"]:
                    raise UnavailableError()
                body = McpReplay.model_validate(dict(client=value))
        elif name == "get_decisions":
            query = parse_page(str(args.get("limit", 50)), args.get("cursor"), evidence=True)
            if query is None:
                raise ValueError()
            after = (
                (
                    datetime.fromisoformat(query.cursor.occurred_at.replace("Z", "+00:00")),
                    UUID(query.cursor.id),
                )
                if query.cursor
                else None
            )
            rows = decisions.decision_rows(
                conn,
                workspace_id=ws,
                user_id=ctx.identity.user_id,
                status="new",
                importance=args.get("importance"),
                kind=args.get("kind"),
                cursor=after,
                limit=query.limit + 1,
            )
            for row in rows:
                if row["client_policy"] == "metadata_only" and (
                    row["evidence_ids"]
                    or not all(
                        isinstance(f, dict) and f.get("basis") == "metadata"
                        for f in row["facts"] or []
                    )
                ):
                    raise UnavailableError()
            items, cursor = page([{**r, "occurred_at": r["created_at"]} for r in rows], query)
            body = DecisionsResponse.model_validate(
                dict(
                    items=[
                        {
                            **{
                                k: r[k]
                                for k in (
                                    "id",
                                    "client_id",
                                    "client_name",
                                    "kind",
                                    "title",
                                    "body",
                                    "play",
                                    "strength",
                                    "priority",
                                    "status",
                                    "importance",
                                )
                            },
                            "week": r["week"].isoformat(),
                            "surfaced_on": r["surfaced_on"].isoformat()
                            if r["surfaced_on"]
                            else None,
                            "created_at": instant(r["created_at"]),
                            "evidence_ids": [str(e) for e in r["evidence_ids"]],
                            "feedback": r["feedback"],
                        }
                        for r in items
                    ],
                    next_cursor=cursor,
                )
            )
        else:
            raise UnavailableError()
    result = body.model_dump(mode="json", by_alias=True, exclude_unset=True)
    count = len(result["items"]) if "items" in result else 1
    if "runs" in result:
        count = len(result["runs"]["items"])
    if name == "get_opportunities":
        count = len(result["expansions"]) + len(result["latent_needs"])
    with ctx.pool.connection() as conn:
        local_mcp.audit(
            conn,
            workspace_id=ws,
            actor_id=ctx.identity.user_id,
            name=name,
            count=count,
            target_id=client_id,
        )
    return result
