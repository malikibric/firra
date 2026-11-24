"""Deterministic importance, merge, feedback suppression and weekly budget."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from uuid import UUID

from firra.assessment.config import risk_rank
from firra.routines.config import RoutinesConfig
from firra.routines.model import Candidate, ClientContext, Decision, Importance, Plan, Prior

IMPORTANCE_ORDER = ("fyi", "attention", "important")
REASON_ORDER = (
    "risk_rose_elevated",
    "risk_rose_watch",
    "risk_rose_other",
    "risk_fell",
    "expansion_strong",
    "expansion_hypothesis",
    "signal_high",
    "signal_opened",
    "signal_resolved",
    "commitment_late_at_risk",
    "commitment_late",
    "client_commitment_late",
    "notice_deadline_close",
    "notice_deadline_near",
)
VERDICT_ORDER = ("wrong", "already_known", "not_now")


def classify(
    candidate: Candidate, context: ClientContext, config: RoutinesConfig
) -> tuple[Importance, str] | None:
    rank = risk_rank(context.risk)
    match candidate.trigger:
        case "risk_up":
            if candidate.risk_to in ("elevated", "high"):
                return "important", "risk_rose_elevated"
            return (
                ("attention", "risk_rose_watch")
                if candidate.risk_to == "watch"
                else ("fyi", "risk_rose_other")
            )
        case "risk_down":
            return "fyi", "risk_fell"
        case "expansion_up":
            return (
                ("attention", "expansion_strong")
                if candidate.expansion_to == "strong"
                else ("fyi", "expansion_hypothesis")
            )
        case "signal_opened":
            return (
                ("attention", "signal_high")
                if candidate.severity == "high" and rank >= 1
                else ("fyi", "signal_opened")
            )
        case "signal_resolved":
            return "fyi", "signal_resolved"
        case "commitment_late":
            if candidate.owner_side == "firm":
                return (
                    ("important", "commitment_late_at_risk")
                    if rank >= 2
                    else ("attention", "commitment_late")
                )
            return "fyi", "client_commitment_late"
        case "notice_close":
            days = context.days_to_notice
            if rank < 1 or days is None or days < 0:
                return None
            if days <= config.notice_close_days:
                return "important", "notice_deadline_close"
            if days <= config.notice_near_days:
                return "attention", "notice_deadline_near"
            return None


def merge(
    candidates: Sequence[Candidate], context: ClientContext, config: RoutinesConfig
) -> Decision | None:
    kept = [(c, result) for c in candidates if (result := classify(c, context, config)) is not None]
    if not kept:
        return None
    winner, (importance, reason) = min(
        kept,
        key=lambda pair: (
            -IMPORTANCE_ORDER.index(pair[1][0]),
            REASON_ORDER.index(pair[1][1]),
            -pair[0].as_of.toordinal(),
            -len(pair[0].evidence_ids),
            pair[0].kind,
            tuple(sorted(map(str, pair[0].evidence_ids))),
        ),
    )
    evidence = frozenset(e for c, _ in kept for e in c.evidence_ids)
    return Decision(
        context.client_id,
        winner.kind,
        reason,
        importance,
        None,
        tuple(sorted({r for _, (_, r) in kept} - {reason})),
        evidence if context.policy == "full" else frozenset(),
        risk_rank(context.risk),
        context.days_to_notice,
        winner.as_of,
    )


def is_suppressed(
    decision: Decision, priors: Sequence[Prior], config: RoutinesConfig, today: date
) -> bool:
    matching = [
        p
        for p in priors
        if p.kind == decision.kind
        and p.reason_code == decision.reason_code
        and p.verdict in VERDICT_ORDER
    ]
    if not matching:
        return False
    prior = min(
        matching,
        key=lambda p: (
            -p.surfaced_on.toordinal(),
            VERDICT_ORDER.index(p.verdict),
            p.risk_rank,
            len(p.evidence_ids),
            tuple(sorted(map(str, p.evidence_ids))),
        ),
    )
    worse = decision.risk_rank > prior.risk_rank
    new = not decision.evidence_ids <= prior.evidence_ids
    if prior.verdict == "wrong" and (today - prior.surfaced_on).days < config.wrong_hold_days:
        return not worse
    return not (worse or new)


def _budget_key(decision: Decision) -> tuple[int, bool, int, int, str]:
    return (
        -decision.risk_rank,
        decision.days_to_notice is None,
        decision.days_to_notice or 0,
        -decision.as_of.toordinal(),
        str(decision.client_id),
    )


def apply_budget(
    decisions: Sequence[Decision], important_used: int, config: RoutinesConfig
) -> tuple[Decision, ...]:
    remaining = max(0, config.budget_per_week - important_used)
    result = []
    for decision in sorted(decisions, key=_budget_key):
        if decision.importance == "important":
            if remaining:
                remaining -= 1
            else:
                decision = replace(decision, importance="attention", demoted_reason="budget")
        result.append(decision)
    return tuple(
        sorted(result, key=lambda d: (-IMPORTANCE_ORDER.index(d.importance), *_budget_key(d)))
    )


def plan(
    candidates: Mapping[UUID, Sequence[Candidate]],
    contexts: Mapping[UUID, ClientContext],
    priors: Mapping[UUID, Sequence[Prior]],
    important_used: int,
    config: RoutinesConfig,
    today: date,
) -> Plan:
    survivors = []
    suppressed = 0
    for client_id, context in contexts.items():
        decision = merge(candidates.get(client_id, ()), context, config)
        if decision is None:
            continue
        if is_suppressed(decision, priors.get(client_id, ()), config, today):
            suppressed += 1
        else:
            survivors.append(decision)
    decisions = apply_budget(survivors, important_used, config)
    return Plan(
        decisions,
        {
            "items": len(decisions),
            "important": sum(d.importance == "important" for d in decisions),
            "demoted": sum(d.demoted_reason == "budget" for d in decisions),
            "suppressed": suppressed,
        },
    )
