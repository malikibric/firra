"""Account assessment (P2b D6): a pure function from the run's drafts and facts to a state.

Signals inside one family are correlated, so a family counts once; the risk state rises when
independent families move together. Escalation is immediate, de-escalation waits for
``deescalate_after`` consecutive runs below the current state. The replay runner calls the same
function week by week, so nothing here reads a clock or the database.
"""

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Any

from firra.assessment.config import (
    AssessmentConfig,
    ExpansionState,
    PlaysConfig,
    RiskState,
    risk_rank,
)
from firra.assessment.model import Assessment, Caveat, Driver, PreviousAssessment
from firra.assessment.plays import choose_play, complaint_windows, days_to_notice
from firra.signals.facts import ClientFacts, ExtractionFact
from firra.signals.model import Severity, SignalDraft

SEVERITY_RANK: dict[Severity, int] = {"low": 1, "medium": 2, "high": 3}

UnresolvedComplaints = Callable[..., Sequence[ExtractionFact]]


def _cap(severity: Severity, cap: Severity) -> Severity:
    return severity if SEVERITY_RANK[severity] <= SEVERITY_RANK[cap] else cap


def _overlaps_holiday(window_start: date, window_end: date, cfg: AssessmentConfig) -> bool:
    for period in cfg.caveats.holiday_periods:
        sm, sd = (int(x) for x in period.start.split("-"))
        em, ed = (int(x) for x in period.end.split("-"))
        for year in (window_start.year - 1, window_start.year, window_end.year):
            start = date(year, sm, sd)
            end = date(year if (em, ed) >= (sm, sd) else year + 1, em, ed)
            if start <= window_end and end >= window_start:
                return True
    return False


def _risk_from_levels(levels: dict[str, Severity]) -> RiskState:
    ranks = sorted((SEVERITY_RANK[s] for s in levels.values()), reverse=True)
    if not ranks:
        return "stable"
    medium_or_more = sum(r >= 2 for r in ranks)
    if ranks[0] == 3 or medium_or_more >= 2:
        return "high"
    if (ranks[0] == 2 and len(ranks) >= 2) or len(ranks) >= 3:
        return "elevated"
    return "watch"


def _step(state: RiskState) -> RiskState:
    order: tuple[RiskState, ...] = ("stable", "watch", "elevated", "high")
    return order[min(risk_rank(state) + 1, len(order) - 1)]


def _timing(state: RiskState, drafts: Sequence[SignalDraft], cfg: AssessmentConfig) -> RiskState:
    """A renewal alone makes a stable client watch; a close notice deadline adds one step."""
    notice = days_to_notice(drafts)
    if not any(d.type == "renewal_window" for d in drafts):
        return state
    if state == "stable":
        return "watch"
    if notice is not None and notice <= cfg.timing.escalate_within_days:
        return _step(state)
    return state


def _hysteresis(
    computed: RiskState, previous: PreviousAssessment | None, as_of: date, cfg: AssessmentConfig
) -> tuple[RiskState, date, int]:
    """(state, state_since, consecutive runs below the state)."""
    if previous is None:
        return computed, as_of, 0
    current = previous.risk_state
    if risk_rank(computed) >= risk_rank(current):
        since = previous.state_since if computed == current else as_of
        return computed, since, 0
    below = previous.below_runs + 1
    if below >= cfg.deescalate_after:
        return computed, as_of, 0
    return current, previous.state_since, below


def _expansion(
    drafts: Sequence[SignalDraft], facts: ClientFacts, cfg: AssessmentConfig
) -> ExpansionState:
    inputs = [d for d in drafts if d.type in cfg.expansion.inputs]
    if not inputs:
        return "none"
    types = {d.type for d in inputs}
    mentions = {str(n.id): n.mention_count for n in facts.needs}
    big_need = any(
        d.type == "expansion_need"
        and mentions.get(d.dedupe_key, 0) >= cfg.expansion.strong_need_mentions
        for d in inputs
    )
    if big_need or (len(types) >= 2 and types & set(cfg.expansion.anchors)):
        return "strong"
    return "hypothesis"


def _recent_positive(facts: ClientFacts, days: int) -> list[str]:
    client_ids = {r.id for r in facts.client_messages()}
    since = facts.ref - timedelta(days=days)
    ids: dict[str, None] = {}
    for x in sorted(facts.extractions, key=lambda e: (e.occurred_at, str(e.id)), reverse=True):
        if x.record_id not in client_ids or not (since < x.occurred_at <= facts.ref):
            continue
        positive = x.kind in {"praise", "result_reported"} or (
            x.kind == "tone" and x.payload.get("tone") == "positive"
        )
        if positive:
            ids.update(dict.fromkeys(str(e) for e in x.evidence_ids))
    return list(ids)


def _strength(drivers: Sequence[Driver], facts: ClientFacts, low_data: bool) -> str:
    """strong: primary drivers span ≥ 2 families and ≥ 2 independent sources; moderate: ≥ 2
    drivers or one evidence driver quoting ≥ 2 records; weak otherwise or with low data."""
    if low_data:
        return "weak"
    thread_by_record = {r.id: r.thread_key for r in facts.records}
    thread_of: dict[Any, str | None] = {}
    record_of: dict[Any, Any] = {}
    for x in facts.extractions:
        for evidence_id in x.evidence_ids:
            thread_of[evidence_id] = thread_by_record.get(x.record_id)
            record_of[evidence_id] = x.record_id
    primary = [d for d in drivers if d.primary]
    sources: set[str] = set()
    for driver in primary:
        if driver.basis == "metadata":
            sources.add(f"metadata:{driver.type}:{driver.dedupe_key}")
            continue
        for evidence_id in driver.evidence_ids:
            thread = thread_of.get(evidence_id)
            sources.add(f"thread:{thread}" if thread else f"evidence:{evidence_id}")
    if len({d.family for d in primary}) >= 2 and len(sources) >= 2:
        return "strong"
    if len(drivers) >= 2:
        return "moderate"
    for driver in drivers:
        records = {record_of.get(e, e) for e in driver.evidence_ids}
        if driver.basis == "evidence" and len(records) >= 2:
            return "moderate"
    return "weak"


def assess(
    drafts: Sequence[SignalDraft],
    facts: ClientFacts,
    previous: PreviousAssessment | None,
    cfg: AssessmentConfig,
    plays: PlaysConfig,
    *,
    volume_types: frozenset[str],
    unresolved_complaints: UnresolvedComplaints,
    guard_caveats: Sequence[Caveat] = (),
) -> Assessment:
    """Assess one client as of ``facts.ref`` from drafts that already passed the coverage guard."""
    as_of = facts.ref.date()
    cav = cfg.caveats
    risk_families = set(cfg.risk_families)

    def is_engagement(d: SignalDraft) -> bool:
        return d.family in {"engagement", "meetings"} or d.type in volume_types

    # The last `holiday_window_days` calendar days, the run date included.
    holiday = any(is_engagement(d) for d in drafts) and _overlaps_holiday(
        as_of - timedelta(days=cav.holiday_window_days - 1), as_of, cfg
    )

    def counted(d: SignalDraft) -> Severity:
        return _cap(d.severity, "low") if holiday and is_engagement(d) else d.severity

    levels: dict[str, Severity] = {}
    for d in drafts:
        if d.family is not None and d.family in risk_families:
            sev = counted(d)
            if d.family not in levels or SEVERITY_RANK[sev] > SEVERITY_RANK[levels[d.family]]:
                levels[d.family] = sev
    computed = _timing(_risk_from_levels(levels), drafts, cfg)
    risk, since, below = _hysteresis(computed, previous, as_of, cfg)

    priority: dict[str, int] = {f: i for i, f in enumerate(cfg.family_priority)}
    ordered = sorted(
        drafts,
        key=lambda d: (
            -SEVERITY_RANK[counted(d)],
            priority.get(d.family or "", len(priority)),
            -d.occurred_at.timestamp(),
            d.type,
            d.dedupe_key,
        ),
    )
    primary_left = cfg.primary_drivers
    drivers: list[Driver] = []
    for d in ordered:
        is_primary = d.family in risk_families and primary_left > 0
        if is_primary:
            primary_left -= 1
        drivers.append(
            Driver(
                type=d.type,
                family=d.family,
                severity=d.severity,
                counted_severity=counted(d),
                basis=d.basis,
                dedupe_key=d.dedupe_key,
                occurred_at=d.occurred_at,
                inputs=d.inputs,
                evidence_ids=d.evidence_ids,
                primary=is_primary,
            )
        )

    recent_messages = sum(
        r.occurred_at > facts.ref - timedelta(days=cav.low_data_window_days)
        for r in facts.client_messages()
    )
    low_data = recent_messages < cav.low_data_min_client_messages
    positive = _recent_positive(facts, cav.recent_positive_days)
    handover_since = facts.ref - timedelta(days=cav.stakeholder_handover_days)
    handover = any(is_engagement(d) for d in drafts) and any(
        d.type in cav.handover_types and d.occurred_at > handover_since for d in drafts
    )
    caveats: list[Caveat] = list(guard_caveats)
    if low_data:
        caveats.append(
            Caveat(
                "low_data",
                "Few client messages in the last 120 days; the assessment rests on little data.",
                {"client_messages": recent_messages},
            )
        )
    if holiday:
        caveats.append(
            Caveat(
                "holiday_period",
                "The last 30 days overlap a holiday period; quieter engagement counts as low.",
            )
        )
    if positive:
        caveats.append(
            Caveat(
                "recent_positive",
                "The client wrote something positive in the last 30 days.",
                {"evidence_ids": positive},
            )
        )
    if handover:
        caveats.append(
            Caveat(
                "stakeholder_handover",
                "A stakeholder change happened recently; quieter engagement may be a handover.",
            )
        )
    if facts.policy == "metadata_only":
        caveats.append(
            Caveat("metadata_only", "This client is analysed from metadata only, without text.")
        )

    expansion = _expansion(drafts, facts, cfg)
    top = next((d for d in drivers if d.primary), None)
    unresolved = {
        w: list(unresolved_complaints(facts, within_days=w)) for w in complaint_windows(plays)
    }
    play = choose_play(
        risk=risk,
        expansion=expansion,
        top=top,
        drafts=drafts,
        unresolved_within={w: bool(items) for w, items in unresolved.items()},
        cfg=plays,
    )
    return Assessment(
        as_of=as_of,
        risk_state=risk,
        previous_risk_state=previous.risk_state if previous else None,
        state_since=since,
        expansion_state=expansion,
        strength=_strength(drivers, facts, low_data),
        drivers=tuple(drivers),
        caveats=tuple(caveats),
        play=play,
        data={
            "family_levels": dict(sorted(levels.items())),
            "computed_risk": computed,
            "below_runs": below,
            "unresolved_complaint_ids": sorted(
                {str(x.id) for items in unresolved.values() for x in items}
            ),
        },
    )


def previous_from_row(row: dict[str, Any]) -> PreviousAssessment:
    """Builds the hysteresis input from a stored assessment row."""
    data = row.get("data") or {}
    return PreviousAssessment(
        as_of=row["as_of"],
        risk_state=row["risk_state"],
        state_since=row["state_since"],
        below_runs=int(data.get("below_runs", 0)),
    )
