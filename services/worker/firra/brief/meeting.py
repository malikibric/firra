"""Deterministic pre-meeting content from stored statements, never evidence quotes."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from firra.memory.unknowns import Unknown
from firra.signals.config import config_dir

COMPLAINT_QUESTION = "The client raised a problem ({text}); is it resolved on their side?"
FIRM_QUESTION = "You said you would {text}; where does it stand?"
CLIENT_QUESTION = "The client said they would {text}; where does it stand?"
STAKEHOLDER_QUESTION = "The stakeholder situation changed ({text}); what needs following up?"
RENEWAL_QUESTION = (
    "Renewal is on {date}, with {days} days to the notice deadline; what needs agreeing?"
)
UNKNOWN_QUESTIONS = {
    "no_stated_goal": "What goal does the client want to achieve?",
    "no_decision_maker": "Who makes the client's decisions?",
    "unknown_renewal_date": "When does the client's contract renew?",
    "no_result_90d": "What recent result has the client reported?",
    "no_champion_contact_30d": "Who is the client champion, and when did you last speak?",
    "budget_unknown": "What budget has the client stated?",
}


class MeetingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["facts.v1"] = "facts.v1"
    lead_minutes: int = Field(default=120, gt=0)
    max_questions: int = Field(default=3, ge=0, le=3)
    stakeholder_days: int = Field(default=30, gt=0)
    renewal_days: int = Field(default=90, gt=0)
    max_items_per_section: int = Field(default=5, gt=0)


def load_config() -> MeetingConfig:
    path = config_dir() / "meeting.yaml"
    return MeetingConfig.model_validate(yaml.safe_load(path.read_text()))


@dataclass(frozen=True)
class BriefItem:
    id: UUID
    text: str
    evidence_ids: tuple[UUID, ...]
    side: Literal["firm", "client"] = "firm"
    due: date | None = None
    late: bool = False
    subtype: str = "other"
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class MeetingFacts:
    client_id: UUID
    ref: datetime
    records: int
    last_held_at: datetime | None
    unknowns: tuple[Unknown, ...] = ()
    commitments: tuple[BriefItem, ...] = ()
    complaints: tuple[BriefItem, ...] = ()
    stakeholders: tuple[BriefItem, ...] = ()
    renewal_date: date | None = None
    notice_period_days: int | None = None


@dataclass(frozen=True)
class BuiltBrief:
    content: dict[str, Any]
    basis: Literal["evidence", "metadata"]
    evidence_ids: tuple[UUID, ...]


def build_content(
    facts: MeetingFacts, *, policy: Literal["full", "metadata_only"], cfg: MeetingConfig
) -> BuiltBrief:
    content: dict[str, Any] = {
        "since_last_meeting": {
            "records": facts.records,
            "last_held_at": facts.last_held_at.isoformat() if facts.last_held_at else None,
        },
        "unknowns": list(facts.unknowns),
        "questions": [],
    }
    questions: list[dict[str, Any]] = content["questions"]
    ids: set[UUID] = set()

    def question(kind: str, ref: UUID | str, text: str) -> None:
        # Skip unsafe wording rather than change the stored claim or expose an address.
        if re.search(r"[\"\u201c\u201d\u2018\u2019`]|[^\s@]+@[^\s@]+|(?<!\w)'[^']+'(?!\w)", text):
            return
        questions.append({"text": text, "basis": {"kind": kind, "ref": str(ref)}})

    def statement(item: BriefItem) -> dict[str, Any]:
        ids.update(item.evidence_ids)
        return {"text": item.text, "evidence_ids": [str(e) for e in item.evidence_ids]}

    if policy == "full":
        limit = cfg.max_items_per_section
        complaints = [i for i in facts.complaints if i.text and i.evidence_ids][:limit]
        commitments = sorted(
            (i for i in facts.commitments if i.text and i.evidence_ids),
            key=lambda i: (i.due or date.max, str(i.id)),
        )[:limit]
        stakeholders = [
            i
            for i in facts.stakeholders
            if i.text
            and i.evidence_ids
            and i.occurred_at is not None
            and facts.ref - timedelta(days=cfg.stakeholder_days) < i.occurred_at <= facts.ref
        ][:limit]
        content["unresolved_complaints"] = [
            {**statement(i), "subtype": i.subtype} for i in complaints
        ]
        content["open_commitments"] = [
            {
                **statement(i),
                "side": i.side,
                "due": i.due.isoformat() if i.due else None,
                "late": i.late,
            }
            for i in commitments
        ]
        content["stakeholder_changes"] = [statement(i) for i in stakeholders]
        for i in complaints:
            question("complaint", i.id, COMPLAINT_QUESTION.format(text=i.text))
        for i in sorted(commitments, key=lambda i: (not i.late, i.due or date.max, str(i.id))):
            template = FIRM_QUESTION if i.side == "firm" else CLIENT_QUESTION
            question("commitment", i.id, template.format(text=i.text))
        for i in stakeholders:
            question("stakeholder", i.id, STAKEHOLDER_QUESTION.format(text=i.text))
    renewal = facts.renewal_date
    if renewal is not None and 0 <= (renewal - facts.ref.date()).days <= cfg.renewal_days:
        days = (renewal - timedelta(days=facts.notice_period_days or 0) - facts.ref.date()).days
        content["renewal"] = {"date": renewal.isoformat(), "days_to_notice": days}
        question("renewal", facts.client_id, RENEWAL_QUESTION.format(date=renewal, days=days))
    for missing in facts.unknowns:
        unknown_template = UNKNOWN_QUESTIONS.get(missing["code"])
        if unknown_template:
            question("unknown", missing["code"], unknown_template)
    content["questions"] = questions[: cfg.max_questions]
    return BuiltBrief(content, "evidence" if ids else "metadata", tuple(sorted(ids, key=str)))
