"""Deterministic meeting facts reused under the authenticated owner's RLS."""

from contextlib import AbstractContextManager
from datetime import datetime
from typing import cast
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.brief.meeting import build_content, load_config
from firra.db.local_evidence import visible
from firra.db.meetings import Meeting, MeetingRepo
from firra.db.types import WorkspaceId
from firra.local.mcp.tools_models import Fact, PreparationResult
from firra.local.readctx import ReadContext


class CallerPool:
    """The existing assembler acquires connections; every acquisition must use owner RLS."""

    def __init__(self, ctx: ReadContext) -> None:
        self.ctx = ctx

    def connection(self) -> AbstractContextManager[Connection[DictRow]]:
        return self.ctx.caller()


def prepare(ctx: ReadContext, client_id: UUID, meeting_at: str | None) -> PreparationResult:
    ws = WorkspaceId(ctx.identity.workspace_id)
    now = ctx.clock()
    at = datetime.fromisoformat(meeting_at.replace("Z", "+00:00")) if meeting_at else now
    if at.tzinfo is None:
        raise ValueError()
    with ctx.caller() as conn:
        client = visible(conn, ws, client_id)
    if client is None:
        raise ValueError()
    cfg = load_config()
    pool = cast(ConnectionPool[Connection[DictRow]], CallerPool(ctx))
    facts = MeetingRepo(pool).load_facts(
        workspace_id=ws, meeting=Meeting(client_id, client_id, at), now=now, cfg=cfg
    )
    if facts is None:
        raise ValueError()
    built = build_content(facts, policy=client["policy"], cfg=cfg)
    content = built.content
    items: list[Fact] = []
    inputs: list[Fact] = []
    sources = {
        "unresolved_complaints": facts.complaints,
        "open_commitments": facts.commitments,
        "stakeholder_changes": facts.stakeholders,
    }
    for section, values in content.items():
        if section == "questions":
            continue
        values = values if isinstance(values, list) else [values]
        for index, value in enumerate(values):
            fields = dict(value)
            evidence_ids = fields.pop("evidence_ids", [])
            source = next(
                (
                    item
                    for item in sources.get(section, ())
                    if item.text == fields.get("text")
                    and list(map(str, item.evidence_ids)) == evidence_ids
                ),
                None,
            )
            key = str(source.id) if source else f"{client_id}:{section}:{fields.get('code', index)}"
            item = Fact(fact_id=key, evidence_ids=evidence_ids, fields={"kind": section, **fields})
            items.append(item)
    sections = {
        "complaint": "unresolved_complaints",
        "commitment": "open_commitments",
        "stakeholder": "stakeholder_changes",
        "renewal": "renewal",
        "unknown": "unknowns",
    }
    for question in content["questions"]:
        basis = question["basis"]
        section = sections[basis["kind"]]
        selected = next(
            (
                f
                for f in items
                if f.fields["kind"] == section
                and (
                    f.fact_id == basis["ref"]
                    or section == "renewal"
                    or (section == "unknowns" and f.fields.get("code") == basis["ref"])
                )
            ),
            None,
        )
        if selected is not None:
            inputs.append(selected)
    with ctx.caller() as conn:
        current = visible(conn, ws, client_id)
    if current is None or current["policy"] != client["policy"]:
        raise ValueError()
    return PreparationResult(
        generated_at=now,
        client_id=client_id,
        meeting_at=at,
        facts=items,
        question_inputs=inputs[: cfg.max_questions],
    )
