"""Replay runner (P2b D8): week-by-week assessments without hindsight.

``replay_series`` is the one loop shared by database replays and the synthetic perfect-extraction
check: facts as of each step (leak-free, T19), rules in memory, the coverage guard, then ``assess``
with the previous step as the series' state. Nothing here writes signals.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta

from firra.assessment.assess import assess
from firra.assessment.complaints import unresolved_complaints
from firra.assessment.config import AssessmentConfig, PlaysConfig
from firra.assessment.guard import coverage_guard
from firra.assessment.model import Assessment, PreviousAssessment
from firra.signals.config import SignalsConfig
from firra.signals.engine import evaluate
from firra.signals.facts import ClientFacts


def end_of_day(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=UTC)


def replay_steps(
    window_start: date, window_end: date, step_days: int, *, stop: date | None = None
) -> list[date]:
    """Steps from ``window_start`` every ``step_days`` up to ``window_end`` (always included);
    a churned client stops at its churn date (the last step is that date)."""
    last = min(window_end, stop) if stop else window_end
    if last < window_start:
        return []
    steps: list[date] = []
    day = window_start
    while day < last:
        steps.append(day)
        day += timedelta(days=step_days)
    steps.append(last)
    return steps


def replay_series(
    load_facts: Callable[[datetime], ClientFacts | None],
    steps: Sequence[date],
    *,
    signals: SignalsConfig,
    assessment: AssessmentConfig,
    plays: PlaysConfig,
    volume_types: frozenset[str],
) -> list[Assessment]:
    tolerance = timedelta(days=assessment.coverage.tolerance_days)
    previous: PreviousAssessment | None = None
    series: list[Assessment] = []
    for step in steps:
        facts = load_facts(end_of_day(step))
        if facts is None:
            continue
        drafts, guard_caveats = coverage_guard(
            facts, evaluate(facts, signals).drafts, volume_types, tolerance=tolerance
        )
        result = assess(
            drafts,
            facts,
            previous,
            assessment,
            plays,
            volume_types=volume_types,
            unresolved_complaints=unresolved_complaints,
            guard_caveats=guard_caveats,
        )
        previous = PreviousAssessment(
            result.as_of, result.risk_state, result.state_since, result.data["below_runs"]
        )
        series.append(result)
    return series
