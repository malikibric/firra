"""Free routine scoring: stored series and cards in; client keys and numbers out."""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from functools import partial
from typing import Any
from uuid import UUID

from firra.assessment.config import RiskState, risk_rank
from firra.assessment.model import Assessment
from firra.assessment.summary import DETECTED_AHEAD_DAYS
from firra.evals.replay_score import CHURN_DETECTED_MIN, EXPANSION_STATES
from firra.routines.config import RoutinesConfig
from firra.routines.engine import merge
from firra.routines.model import Candidate, ClientContext, Decision, Trigger


@dataclass(frozen=True)
class CommitmentInput:
    stated_on: date
    due_date: date | None
    owner_side: str
    evidence_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True)
class ClientSeries:
    key: str
    assessments: Sequence[Assessment]
    policy: str = "full"
    renewal_date: date | None = None
    notice_period_days: int | None = None
    churned_at: date | None = None
    notice_at: date | None = None
    max_risk: RiskState | None = None
    commitments: Sequence[CommitmentInput] = ()


@dataclass(frozen=True)
class CardObs:
    client: str
    day: date
    routine: str
    kind: str
    reason_code: str
    importance: str
    risk_rank: int
    demoted_reason: str | None = None
    evidence_ids: frozenset[UUID] = frozenset()
    fact_bases: tuple[str, ...] = ()
    not_now: bool = False


@dataclass(frozen=True)
class RoutineEvalResult:
    gates: dict[str, bool]
    metrics: dict[str, Any]
    findings: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return asdict(self) | {"passed": all(self.gates.values())}


def expected_changes(
    clients: Sequence[ClientSeries], days: Sequence[date], config: RoutinesConfig
) -> dict[tuple[str, date, str], Decision]:
    """Independent scan-window selection; reuse the engine only for classification/merge."""
    result = {}
    previous_day = None
    for day in sorted(days):
        since = max(previous_day or day - timedelta(days=1), day - timedelta(days=7))
        previous_day = day
        for index, client in enumerate(clients, 1):
            if client.policy == "excluded":
                continue
            history = sorted(
                (a for a in client.assessments if a.as_of <= day), key=lambda a: a.as_of
            )
            if not history:
                continue
            latest = history[-1]
            cid = UUID(int=index)
            deadline = (
                client.renewal_date - timedelta(days=client.notice_period_days)
                if client.renewal_date and client.notice_period_days is not None
                else None
            )
            context = ClientContext(
                cid,
                "metadata_only" if client.policy == "metadata_only" else "full",
                latest.risk_state,
                (deadline - day).days if deadline else None,
            )
            candidate = partial(Candidate, cid, as_of=day)
            scan = []
            for i, row in enumerate(history):
                if row.as_of <= since:
                    continue
                old = row.previous_risk_state
                if old is not None and old != row.risk_state:
                    trigger: Trigger = (
                        "risk_up" if risk_rank(row.risk_state) > risk_rank(old) else "risk_down"
                    )
                    scan.append(candidate("state_change", trigger, risk_to=row.risk_state))
                if i and EXPANSION_STATES.index(row.expansion_state) > EXPANSION_STATES.index(
                    history[i - 1].expansion_state
                ):
                    scan.append(
                        candidate("expansion", "expansion_up", expansion_to=row.expansion_state)
                    )
            now = (deadline - day).days if deadline else -1
            before = (deadline - since).days if deadline else -1
            limits = (config.notice_near_days, config.notice_close_days)
            if now >= 0 and any(before > limit >= now for limit in limits):
                scan.append(candidate("state_change", "notice_close"))
            watch = [
                candidate(
                    "commitment_watch",
                    "commitment_late",
                    owner_side="firm" if c.owner_side == "firm" else "client",
                    evidence_ids=c.evidence_ids,
                )
                for c in client.commitments
                if client.policy == "full"
                and c.stated_on <= day
                and c.due_date is not None
                and since <= c.due_date < day
            ]
            for routine, candidates in (("morning_scan", scan), ("commitment_watch", watch)):
                decision = merge(candidates, context, config)
                if decision:
                    result[client.key, day, routine] = decision
    return result


def repeats_prior(
    client: str, day: date, kind: str, reason: str, rank: int, prior: CardObs
) -> bool:
    return (
        (client, kind, reason) == (prior.client, prior.kind, prior.reason_code)
        and day > prior.day
        and rank <= prior.risk_rank
    )


def score_routines(
    clients: Sequence[ClientSeries],
    cards: Sequence[CardObs],
    days: Sequence[date],
    *,
    config: RoutinesConfig,
    feedback: bool = False,
) -> RoutineEvalResult:
    expected = expected_changes(clients, days, config)
    observed = {(c.client, c.day, c.routine): c for c in cards}
    findings: dict[str, list[dict[str, Any]]] = {}

    def fail(gate: str, client: str, day: date) -> None:
        findings.setdefault(gate, []).append(
            {
                "client": client,
                "day": day.isoformat(),
                "week": (day - timedelta(days=day.weekday())).isoformat(),
            }
        )

    scans = {key: value for key, value in expected.items() if key[2] == "morning_scan"}
    actual_scans = {key: value for key, value in observed.items() if key[2] == "morning_scan"}
    fp, fn = actual_scans.keys() - scans.keys(), scans.keys() - actual_scans.keys()
    mismatches = [
        key
        for key in scans.keys() & actual_scans.keys()
        if (scans[key].kind, scans[key].reason_code)
        != (actual_scans[key].kind, actual_scans[key].reason_code)
    ]
    if not feedback:
        for key, day, _ in sorted(fp | fn | set(mismatches)):
            fail("exactness", key, day)
    weeks = Counter(
        (c.day - timedelta(days=c.day.weekday())).isoformat()
        for c in cards
        if c.importance == "important"
    )
    for card in cards:
        week = (card.day - timedelta(days=card.day.weekday())).isoformat()
        if weeks[week] > config.budget_per_week:
            fail("budget", card.client, card.day)
    silenced = [c for c in cards if c.not_now]
    missing_demotions = 0
    for (key, day, routine), decision in expected.items():
        if decision.importance != "important":
            continue
        # Budget checks account for valid suppression, including the engine's new-evidence escape.
        suppressed = feedback and any(
            repeats_prior(key, day, decision.kind, decision.reason_code, decision.risk_rank, p)
            and decision.evidence_ids <= p.evidence_ids
            for p in silenced
        )
        if suppressed:
            continue
        got = observed.get((key, day, routine))
        if got is None or not (
            got.importance == "important"
            or (got.importance == "attention" and got.demoted_reason == "budget")
        ):
            missing_demotions += 1
            fail("budget", key, day)
    repeats = 0
    for card in cards:
        if feedback and any(
            repeats_prior(card.client, card.day, card.kind, card.reason_code, card.risk_rank, p)
            for p in silenced
        ):
            repeats += 1
            fail("suppression", card.client, card.day)
    leads: dict[str, int | None] = {}
    benign_checked = metadata_cards = 0
    for client in clients:
        own = [c for c in cards if c.client == client.key]
        important = [c for c in own if c.importance == "important"]
        if client.churned_at is not None:
            anchor = client.notice_at or client.churned_at
            leads[client.key] = (anchor - min(c.day for c in important)).days if important else None
        if client.max_risk is not None and risk_rank(client.max_risk) < risk_rank("elevated"):
            benign_checked += 1
            for card in important:
                fail("benign", client.key, card.day)
        for card in own:
            if client.policy == "metadata_only":
                metadata_cards += 1
            invalid_metadata = card.evidence_ids or set(card.fact_bases) != {"metadata"}
            if client.policy == "excluded" or (
                client.policy == "metadata_only" and invalid_metadata
            ):
                fail("policy", client.key, card.day)
    detected = sum(lead is not None and lead >= DETECTED_AHEAD_DAYS for lead in leads.values())
    numerator, denominator = CHURN_DETECTED_MIN
    timing_ok = detected * denominator >= len(leads) * numerator
    if not feedback and not timing_ok:
        for client in clients:
            lead = leads.get(client.key)
            if client.key in leads and (lead is None or lead < DETECTED_AHEAD_DAYS):
                fail("timing", client.key, client.notice_at or client.churned_at or max(days))
    gates = {
        name: name not in findings
        for name in (
            ["budget", "suppression"]
            if feedback
            else ["exactness", "budget", "timing", "benign", "policy"]
        )
    }
    worst = min(weeks, key=lambda w: (-weeks[w], w)) if weeks else None
    return RoutineEvalResult(
        gates,
        {
            "cards": len(cards),
            "run_days": len(days),
            "false_positives": None if feedback else len(fp),
            "false_negatives": None if feedback else len(fn),
            "reason_mismatches": None if feedback else len(mismatches),
            "weeks": len({(d - timedelta(days=d.weekday())).isoformat() for d in days}),
            "worst_week": worst,
            "worst_week_important": weeks[worst] if worst else 0,
            "demoted": sum(c.demoted_reason == "budget" for c in cards),
            "missing_demotions": missing_demotions,
            "feedback": len(silenced),
            "repeats": repeats,
            "lead_days": leads,
            "churn_detected_30d": detected,
            "churn_checked": len(leads),
            "benign_clients": benign_checked,
            "metadata_cards": metadata_cards,
            "excluded_clients": sum(c.policy == "excluded" for c in clients),
        },
        findings,
    )


def score_suppression_probe(
    probes: Mapping[str, date],
    without_feedback: Sequence[CardObs],
    with_feedback: Sequence[CardObs],
) -> RoutineEvalResult:
    """Require each repeated elevated transition to surface only without feedback."""

    def present(cards: Sequence[CardObs], key: str, day: date) -> bool:
        return any(
            (c.client, c.day, c.routine, c.kind, c.reason_code)
            == (key, day, "morning_scan", "state_change", "risk_rose_elevated")
            for c in cards
        )

    missing = [key for key, day in probes.items() if not present(without_feedback, key, day)]
    repeated = [key for key, day in probes.items() if present(with_feedback, key, day)]
    failures = [
        {
            "client": key,
            "day": probes[key].isoformat(),
            "week": (probes[key] - timedelta(days=probes[key].weekday())).isoformat(),
        }
        for key in sorted(set(missing + repeated))
    ]
    return RoutineEvalResult(
        {"suppression": bool(probes) and not failures},
        {
            "probe_repeats_suppressed": len(probes) - len(repeated),
            "probe_repeats_without_feedback": len(probes) - len(missing),
        },
        {"suppression": failures} if failures else {},
    )
