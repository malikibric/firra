"""Churn Replay report as Markdown (roadmap milestone B): the founder-facing walk-through.

Deterministic text from the narrative plus verified quotes (≤ 25 words each, from `evidence`).
A `metadata_only` client has no quotes and the report says so. Every date is a fact from the
replay; the report never claims more than the stored drivers show.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from firra.assessment.config import risk_rank
from firra.assessment.narrative import DriverView, Narrative

MAX_QUOTES_PER_DRIVER = 2

# Plain next steps per play: rule-based suggestions, not predictions of what would prevent a loss.
NEXT_STEPS: dict[str, str] = {
    "fix_first": "Check the open complaint or late commitment with the account lead and agree a "
    "fix date with the client before selling anything.",
    "stakeholder_remap": "Find out who now decides on the client side and make contact.",
    "renewal_prep": "Clarify scope and budget for the next term before the notice deadline.",
    "founder_review_call": "Review the account yourself and check its context before acting.",
    "expansion_conversation": "Raise the repeated ask with the client as a possible new service.",
    "monitor": "No action needed now; keep watching.",
}

HOW_TO_READ = [
    "States: stable (nothing material), watch (one or two weak signs), elevated (several signs "
    "or one strong sign), high (strong signs on several fronts).",
    "A first alert is the first week at elevated or high that the following week confirms. Lead "
    "time is the calendar distance to the recorded notice or churn date. It is not a forecast and "
    "does not show that a loss could have been prevented.",
    "Evidence strength describes how well the rules are backed by quoted messages, not the "
    "chance that a client leaves.",
    "A signal can drop out of a rolling window without the underlying issue being resolved.",
]


@dataclass(frozen=True, slots=True)
class QuoteView:
    quote: str
    occurred_at: date | None
    deep_link: str | None
    author_name: str | None = None
    author_role: str | None = None
    subject: str | None = None
    source_ref: str | None = None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _quotes(driver: DriverView, evidence: Mapping[UUID, QuoteView], shown: set[UUID]) -> list[str]:
    lines: list[str] = []
    repeated = False
    for evidence_id in driver.evidence_ids[:MAX_QUOTES_PER_DRIVER]:
        item = evidence.get(evidence_id)
        if item is None:
            continue
        if evidence_id in shown:
            repeated = True
            continue
        shown.add(evidence_id)
        author = ", ".join(p for p in (item.author_name, item.author_role) if p)
        context = " · ".join(
            p
            for p in (
                author,
                f'"{item.subject}"' if item.subject else None,
                item.occurred_at.isoformat() if item.occurred_at else None,
                f"source {item.source_ref}" if item.source_ref else None,
            )
            if p
        )
        link = f"[open]({item.deep_link})" if item.deep_link else "source link unavailable"
        lines.append(f'  - "{item.quote}"' + (f" — {context}" if context else "") + f" · {link}")
    if repeated and not lines:
        lines.append("  - Same message as quoted above.")
    return lines


def _driver_lines(
    drivers: Sequence[DriverView],
    evidence: Mapping[UUID, QuoteView],
    shown: set[UUID],
    new: Sequence[DriverView] = (),
    grouping_ids: Mapping[DriverView, tuple[UUID, ...]] | None = None,
) -> list[str]:
    groups: list[list[DriverView]] = []
    keys = grouping_ids or {}
    for driver in drivers:
        group = next(
            (
                g
                for g in groups
                if driver.evidence_ids
                and set(keys.get(g[0], g[0].evidence_ids))
                == set(keys.get(driver, driver.evidence_ids))
            ),
            None,
        )
        if group is None:
            groups.append([driver])
        else:
            group.append(driver)
    lines: list[str] = []
    for group in groups:
        labels = "; ".join(d.sentence + (" [new]" if d in new else "") for d in group)
        when = f" — {group[0].occurred_at.isoformat()}" if group[0].occurred_at else ""
        lines.append(f"- {labels}{when}")
        if len(group) > 1:
            lines.append(f"  - One message, read by {len(group)} rules.")
        lines.extend(_quotes(group[0], evidence, shown))
    return lines


def _headline(n: Narrative) -> str:
    if n.status == "churned":
        end = f"the {n.end_kind} on {n.end_date.isoformat()}" if n.end_date else "the churn"
        if n.detection_at is None:
            return (
                f"Firra would not have raised an alert for {n.client_name} before {end}. "
                "The section below shows what was visible."
            )
        confirmed = (
            f" The following week ({n.confirmed_at.isoformat()}) confirmed it, "
            f"{n.confirmed_lead_days} days before {end}."
            if n.confirmed_at and n.confirmed_lead_days is not None
            else ""
        )
        return (
            f"First alert: {n.client_name} moved to **{n.detection_state}** in the week of "
            f"{n.detection_at.isoformat()}, **{n.lead_days} days before {end}**.{confirmed}"
        )
    if n.detection_at is None:
        return f"{n.client_name} never stayed at elevated or high for two weeks in a row."
    return (
        f"{n.client_name} was at elevated or high from {n.detection_at.isoformat()} "
        f"({_plural(n.weeks_elevated, 'assessment')} in the window). Staying a client does not "
        "show the concern was unfounded."
    )


def _unconfirmed(n: Narrative) -> str | None:
    if not n.unconfirmed_at_end or n.last_as_of is None:
        return None
    return (
        f"The latest assessment ({n.last_as_of.isoformat()}) is **{n.last_state}**. The replay "
        "ends there, so no following week confirms it: check the account before acting."
    )


def render_client(
    n: Narrative,
    evidence: Mapping[UUID, QuoteView],
    *,
    window: tuple[date, date],
    metadata_only: bool = False,
) -> str:
    shown: set[UUID] = set()
    if metadata_only:
        evidence = {}
    lines = [f"# {n.client_name} — Churn Replay", ""]
    if metadata_only:
        lines += [
            "_Metadata only: no message content was read or stored for this client. Findings "
            "come from counts, dates and meetings._",
            "",
        ]
    lines += [
        f"Window {window[0].isoformat()} to {window[1].isoformat()}, one assessment per week. Each "
        "week uses only what existed that day (no hindsight in the inputs).",
        "",
        _headline(n),
        "",
    ]
    if note := _unconfirmed(n):
        lines += [note, ""]
    if n.visible_at_detection:
        lines += [f"## What was visible on {n.detection_at.isoformat()}", ""]  # type: ignore[union-attr]
        lines += _driver_lines((*n.visible_at_detection, *n.detection_timing), evidence, shown)
        if n.detection_play:
            lines += ["", f"Suggested next step: {NEXT_STEPS[n.detection_play]}"]
        lines.append("")
    lines += ["## Timeline", ""]
    if not n.transitions:
        lines += ["The state stayed stable for the whole window.", ""]
    dates = sorted({t.as_of for t in n.transitions} | {e.as_of for e in n.events})
    outcomes = [e.as_of for e in n.events if e.kind in {"notice", "churn", "first_known"}]
    for at in dates:
        transitions = [t for t in n.transitions if t.as_of == at]
        if not transitions:
            lines += [f"### {at.isoformat()}", ""]
        for t in transitions:
            before = t.from_state or "no data"
            ahead = (
                f", {t.days_before_end} days before the {n.end_kind}"
                if t.days_before_end and t.days_before_end > 0
                else ""
            )
            lines += [f"### {at.isoformat()}: {before} → {t.to_state}{ahead}", ""]
            lines += _driver_lines(t.active_drivers, evidence, shown, t.new_drivers)
            if t.only_ended:
                lines.append(
                    "The state drop reflects only drivers no longer counted; "
                    "rolling windows do not establish resolution."
                )
            dropped = t.from_state is not None and risk_rank(t.to_state) < risk_rank(t.from_state)
            if dropped and outcomes and at >= min(outcomes):
                lines.append(
                    "A recorded loss outcome is already on the timeline; "
                    "this state does not establish recovery."
                )
            next_step = NEXT_STEPS[t.play]
            if (
                t.play == "monitor"
                and t.from_state
                and risk_rank(t.to_state) < risk_rank(t.from_state)
            ):
                next_step = (
                    "Check whether earlier issues were addressed; "
                    "a lower state alone does not establish resolution."
                )
            lines.append(f"Evidence strength: {t.strength}. Suggested next step: {next_step}")
            lines.extend(f"- Caveat: {code.replace('_', ' ')}" for code in t.caveats)
        updates = [e for e in n.events if e.as_of == at]
        added = [e.driver for e in updates if e.kind in {"new_evidence", "new_driver"} and e.driver]
        if added:
            lines.append("Signal updates:")
            groups = {
                e.driver: e.grouping_ids or e.driver.evidence_ids for e in updates if e.driver
            }
            lines += _driver_lines(added, evidence, shown, grouping_ids=groups)
        for event in updates:
            if event.kind == "ended" and event.driver:
                lines.append(
                    f"- No longer counted from {at}: {event.driver.label}. "
                    "Rolling windows let signals age out; "
                    "this does not show the issue was resolved."
                )
            elif event.kind == "severity_changed" and event.driver:
                lines.append(
                    f"- Severity changed: {event.driver.label}: "
                    f"{event.old_severity} → {event.driver.severity}."
                )
            elif event.kind in {"notice", "churn", "first_known"}:
                label = {
                    "notice": "Recorded notice date",
                    "churn": "Recorded churn date",
                    "first_known": "The firm learned of the loss",
                }[event.kind]
                lines.append(f"- {label}: {at}.")
        lines.append("")
    lines += ["## How to read this", "", *(f"- {item}" for item in HOW_TO_READ), ""]
    lines += [
        "---",
        "Generated by Firra from stored facts only. Quotes are verbatim from the source messages "
        "(at most 25 words). Nothing here is a score of any person.",
    ]
    return "\n".join(lines) + "\n"


def render_index(
    narratives: Sequence[Narrative],
    *,
    window: tuple[date, date],
    totals: Mapping[str, object],
    filenames: Sequence[str] | None = None,
) -> str:
    files = dict(zip((id(n) for n in narratives), filenames or (), strict=False))

    def name(n: Narrative) -> str:
        target = files.get(id(n))
        return f"[{n.client_name}]({target})" if target else n.client_name

    lost = [n for n in narratives if n.status == "churned"]
    kept = [n for n in narratives if n.status != "churned"]
    lines = [
        "# Churn Replay — overview",
        "",
        f"Window {window[0].isoformat()} to {window[1].isoformat()}.",
        "",
        "## Lost clients",
        "",
        "| Client | First alert | Confirmed | Days before | Peak | Miss reason |",
        "|---|---|---|---|---|---|",
    ]
    for n in sorted(lost, key=lambda x: x.client_name):
        days = n.lead_days if n.lead_days is not None else "—"
        lines.append(
            f"| {name(n)} | {n.detection_at or '—'} | {n.confirmed_at or '—'} | {days} "
            f"| {n.max_state or '—'} | {'—' if n.detection_at else 'no alert'} |"
        )
    lines += ["", "## Retained clients (concerns raised although the client stayed)", ""]
    lines += [
        "| Client | Peak | Elevated or high assessments | Latest state |",
        "|---|---|---|---|",
    ]
    for n in sorted(kept, key=lambda x: x.client_name):
        latest = f"{n.last_state or '—'}" + (" (unconfirmed)" if n.unconfirmed_at_end else "")
        lines.append(f"| {name(n)} | {n.max_state or '—'} | {n.weeks_elevated} | {latest} |")
    months = max((window[1] - window[0]).days, 1) / (365.25 / 12)
    alarms = sum(n.weeks_elevated for n in kept)
    lines += [
        "",
        f"Losses detected 30+ days ahead: {totals.get('losses_detected_30d', 0)} of "
        f"{totals.get('losses', 0)}. Median lead: {totals.get('median_lead_days', '—')} days.",
        "",
        "Retained clients with concerns per 10 clients per month: "
        f"{totals.get('false_alarm_client_weeks_per_10_clients_per_month', 0)} "
        f"({alarms} elevated or high assessments, {len(kept)} retained clients, {months:.1f} "
        "months). Whether those concerns were justified or already handled is not known from "
        "this replay.",
        "",
        "## How to read this",
        "",
        *(f"- {item}" for item in HOW_TO_READ),
        "",
    ]
    return "\n".join(lines)
