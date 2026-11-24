"""Timing-aware scoring of one replay run against the dated expectations in truth (P2b D12).

Pure: weekly observations in, metrics out. Client keys and numbers only; no quotes or ids.
"""

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from statistics import median

from firra.assessment.config import risk_rank
from firra.assessment.summary import ELEVATED, Step, detection, flicker
from firra.evals.models import AssessmentObs, ReplayEvalResult, Truth

ACCURACY_MIN = 0.8
FALSE_ALARM_MAX = 1.0
# At least two of three churned clients; kept as a ratio so a larger corpus scales.
CHURN_DETECTED_MIN = (2, 3)
EXPANSION_STATES = ("none", "hypothesis", "strong")


def _expansion_rank(state: str) -> int:
    return EXPANSION_STATES.index(state)


def _share_ok(correct: int, checked: int) -> bool:
    return checked == 0 or correct / checked >= ACCURACY_MIN


def _risk_of(obs: AssessmentObs) -> int:
    return risk_rank(obs.risk)


def _expansion_of(obs: AssessmentObs) -> int:
    return _expansion_rank(obs.expansion)


def _first_reaching(
    series: Sequence[AssessmentObs], rank_of: Callable[[AssessmentObs], int], minimum: int
) -> date | None:
    return next((o.as_of for o in series if rank_of(o) >= minimum), None)


def score_replay(
    observations: Sequence[AssessmentObs],
    truth: Truth,
    *,
    step_days: int = 7,
    lead_days: Mapping[str, int | None] | None = None,
) -> ReplayEvalResult:
    """``lead_days`` (client key -> days before notice or churn, from the replay summary) is
    reported for the losses; the pass rule uses ``detect_by``, which is dated 30 days ahead."""
    keys = {client.name: key for key, client in truth.clients.items()}
    by_client: dict[str, list[AssessmentObs]] = defaultdict(list)
    for obs in observations:
        if obs.client in keys:
            by_client[keys[obs.client]].append(obs)
    for series in by_client.values():
        series.sort(key=lambda o: o.as_of)

    failures: dict[str, list[str]] = defaultdict(list)
    counts = {"risk": [0, 0], "expansion": [0, 0], "play": [0, 0]}
    by_met: dict[str, bool] = {}
    churned_detected = churned_checked = 0
    max_risk_exceeded: list[str] = []
    caveats_missing: dict[str, list[str]] = {}
    alarm_weeks = 0
    flicker_total = 0
    active_clients = 0

    for key, client in sorted(truth.clients.items()):
        series = by_client.get(key, [])
        steps = [Step(o.as_of, o.risk) for o in series]
        flicker_count = flicker(steps)
        flicker_total += flicker_count
        if flicker_count:
            failures["flicker"].append(key)
        if client.status == "churned":
            if client.detect_by is None:
                # A loss without a deadline must not shrink the denominator of the gate.
                failures["churn_without_detect_by"].append(key)
                continue
            churned_checked += 1
            detected = detection(steps)
            if detected is not None and detected <= client.detect_by:
                churned_detected += 1
            else:
                failures["churn_not_detected_in_time"].append(key)
            continue
        active_clients += 1
        expected = client.assessment
        if client.max_risk is not None and any(
            risk_rank(o.risk) > risk_rank(client.max_risk) for o in series
        ):
            max_risk_exceeded.append(key)
        if expected is None:
            continue
        if risk_rank(expected.risk) < ELEVATED:
            alarm_weeks += sum(risk_rank(o.risk) >= ELEVATED for o in series)
        final = series[-1] if series else None
        if final is None:
            failures["no_observations"].append(key)
        for name, got, want in (
            ("risk", final and final.risk, expected.risk),
            ("expansion", final and final.expansion, expected.expansion),
            ("play", final and final.play, expected.play),
        ):
            counts[name][1] += 1
            if got == want:
                counts[name][0] += 1
            else:
                failures[f"{name}_state" if name != "play" else "play"].append(key)
        if final is not None:
            missing = sorted(set(client.caveats) - set(final.caveats))
            if missing:
                caveats_missing[key] = missing
        if expected.by is not None:
            risk_min, expansion_min = risk_rank(expected.risk), _expansion_rank(expected.expansion)
            met = True
            if risk_min:
                day = _first_reaching(series, _risk_of, risk_min)
                met = met and day is not None and day <= expected.by
            if expansion_min:
                day = _first_reaching(series, _expansion_of, expansion_min)
                met = met and day is not None and day <= expected.by
            by_met[key] = met
            if not met:
                failures["by_not_met"].append(key)

    dates = [o.as_of for series in by_client.values() for o in series]
    months = max((max(dates) - min(dates)).days, 1) / (365.25 / 12) if dates else 1.0
    per_10 = (
        round(alarm_weeks * step_days / 7 / (active_clients / 10) / months, 3)
        if active_clients
        else 0.0
    )
    if max_risk_exceeded:
        failures["max_risk_exceeded"] = max_risk_exceeded
    numerator, denominator = CHURN_DETECTED_MIN
    passed = (
        all(_share_ok(*counts[name]) for name in counts)
        and all(by_met.values())
        and churned_detected * denominator >= churned_checked * numerator
        and not max_risk_exceeded
        and per_10 <= FALSE_ALARM_MAX
        and flicker_total == 0
        and not failures.get("no_observations")
        and not failures.get("churn_without_detect_by")
    )
    leads = {key: days for key, days in sorted((lead_days or {}).items()) if key in truth.clients}
    known = [d for d in leads.values() if d is not None]
    return ReplayEvalResult(
        risk_correct=counts["risk"][0],
        risk_checked=counts["risk"][1],
        expansion_correct=counts["expansion"][0],
        expansion_checked=counts["expansion"][1],
        play_correct=counts["play"][0],
        play_checked=counts["play"][1],
        by_met=by_met,
        churned_detected=churned_detected,
        churned_checked=churned_checked,
        lead_days=leads,
        median_lead_days=median(known) if known else None,
        max_risk_exceeded=max_risk_exceeded,
        caveats_missing=caveats_missing,
        false_alarm_weeks_per_10_per_month=per_10,
        flicker=flicker_total,
        failures={k: sorted(v) for k, v in failures.items()},
        passed=passed,
    )
