"""A reported result is less than half the most recent comparable stated goal."""

import re
from datetime import timedelta
from uuid import UUID

from pydantic import Field

from firra.signals.facts import ClientFacts, ExtractionFact
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window

NUMBER = re.compile(r"(?<!\d)(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\d)")


class GoalResultMismatchConfig(RuleConfig):
    pairing_days: int = Field(gt=0)
    max_result_ratio: float = Field(gt=0)
    lower_is_better_terms: tuple[str, ...]
    severity: Severity


def _number(text: str) -> float | None:
    match = NUMBER.search(text)
    return float(match.group().replace(",", "")) if match else None


def evaluate(facts: ClientFacts, cfg: GoalResultMismatchConfig) -> list[SignalDraft]:
    goals = sorted(
        (e for e in facts.extractions if e.kind == "goal" and e.occurred_at <= facts.ref),
        key=lambda e: (e.occurred_at, str(e.id)),
    )
    results = sorted(
        (
            e
            for e in facts.extractions
            if e.kind == "result_reported" and e.occurred_at <= facts.ref
        ),
        key=lambda e: (e.occurred_at, str(e.id)),
    )
    mismatch: dict[UUID, tuple[ExtractionFact, list[ExtractionFact], float, float]] = {}
    for result in results:
        paired = [
            goal
            for goal in goals
            if goal.occurred_at <= result.occurred_at
            and (
                in_window(goal.occurred_at, result.occurred_at, newer_than_days=cfg.pairing_days)
                or goal.occurred_at == result.occurred_at - timedelta(days=cfg.pairing_days)
            )
        ]
        if not paired:
            continue
        goal = paired[-1]
        goal_text = str(goal.payload.get("text", ""))
        if any(
            re.search(r"\b" + re.escape(term) + r"\b", goal_text, re.I)
            for term in cfg.lower_is_better_terms
        ):
            continue
        goal_number = _number(goal_text)
        result_number = _number(str(result.payload.get("text", "")))
        if (
            goal_number is None
            or result_number is None
            or goal_number <= 0
            or result_number >= cfg.max_result_ratio * goal_number
            or not goal.evidence_ids
            or not result.evidence_ids
        ):
            continue
        if goal.record_id not in mismatch:
            mismatch[goal.record_id] = (goal, [], goal_number, result_number)
        mismatch[goal.record_id][1].append(result)
        mismatch[goal.record_id] = (goal, mismatch[goal.record_id][1], goal_number, result_number)
    drafts = []
    for record_id, (goal, reported, goal_number, result_number) in sorted(
        mismatch.items(), key=lambda pair: str(pair[0])
    ):
        evidence = tuple(
            dict.fromkeys(
                [*goal.evidence_ids, *(eid for item in reported for eid in item.evidence_ids)]
            )
        )
        drafts.append(
            SignalDraft(
                family=cfg.family,
                type="goal_result_mismatch",
                severity=cfg.severity,
                occurred_at=reported[-1].occurred_at,
                basis="evidence",
                dedupe_key=str(record_id),
                evidence_ids=evidence,
                inputs={"goal": goal_number, "result": result_number},
            )
        )
    return drafts


SPEC = RuleSpec(GoalResultMismatchConfig, evaluate)
