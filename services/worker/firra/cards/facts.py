"""Deterministic card facts from an assessment (P2b D9).

Every number and date a card may mention comes from here; the model only words what these facts
say. Sentences are English (O-D7). Evidence facts carry the verified quotes for the model's
understanding; the stored card keeps only their evidence ids.
"""

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from uuid import UUID

from firra.assessment.model import Assessment, Driver
from firra.cards.schema import Fact

LABELS: dict[str, str] = {
    "delivery_complaint": "The client complained about delivery",
    "repeated_complaint": "The client complained repeatedly",
    "executive_escalation": "A senior client stakeholder raised a complaint",
    "tone_decline": "The tone of the client's messages turned more negative",
    "champion_departure": "The client's main contact is leaving or has left",
    "champion_latency_drift": "The client's main contact replies more slowly",
    "new_senior_stakeholder": "A new senior stakeholder appeared on the client side",
    "reduced_engagement": "The client writes less than before",
    "client_initiation_drop": "The client starts fewer conversations than before",
    "client_reply_latency_drift": "The client replies more slowly than before",
    "participant_narrowing": "Fewer people on the client side are writing",
    "decision_maker_silence": "No recent message from a client decision maker",
    "meeting_cancellations": "Meetings with the client were cancelled or moved",
    "meeting_attendance_drop": "Fewer people attend meetings with the client",
    "late_commitment": "A commitment is past its due date",
    "goal_result_mismatch": "Reported results fall short of the client's stated goal",
    "first_90_days_no_result": "No result has been reported since the engagement started",
    "in_house_risk": "The client mentioned doing the work in-house or with another vendor",
    "scope_cut_talk": "The client talked about cutting budget or scope",
    "renewal_window": "The contract renewal is approaching",
    "expansion_need": "The client keeps asking for something the firm offers",
    "client_initiative": "The client described a new business initiative",
    "budget_growth": "The client talked about a bigger budget or more work",
}

INPUT_LABELS: dict[str, str] = {
    "messages_30": "client messages in the last 30 days",
    "baseline_per_30": "usual messages per 30 days",
    "initiated_30": "conversations started by the client in the last 30 days",
    "recent_median_h": "median reply time now (hours)",
    "base_median_h": "median reply time before (hours)",
    "recent_n": "recent replies",
    "base_n": "earlier replies",
    "authors_60": "client people writing in the last 60 days",
    "baseline_authors_per_60": "usual client people writing per 60 days",
    "mean_90": "average attendees in the last 90 days",
    "base_mean": "average attendees before",
    "share_30": "share of negative messages in the last 30 days",
    "share_base": "share of negative messages before",
    "last_message_at": "last message",
    "messages_31_180": "earlier messages",
    "renewal_date": "renewal date",
    "notice_deadline": "notice deadline",
    "days_to_notice": "days until the notice deadline",
    "contract_start": "engagement start",
    "days": "days since the start",
    "affected": "cancelled or moved meetings",
    "total": "meetings in the window",
    "share": "share of meetings affected",
    "overdue_days": "days overdue",
    "due_date": "due date",
}

# Input keys shared by several drivers that measure different things (stored data keeps the key).
TYPE_INPUT_LABELS: dict[tuple[str, str], str] = {
    ("client_initiation_drop", "baseline_per_30"): (
        "usual conversations started by the client per 30 days"
    ),
}


def _value(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float):
        return f"{value:g}" if value != int(value) else str(int(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            return value if len(value) <= 40 else None
    return None


def inputs_text(inputs: Mapping[str, Any] | None, driver_type: str | None = None) -> str:
    parts: list[str] = []
    for key, raw in (inputs or {}).items():
        if key.endswith("_id") or key not in INPUT_LABELS:
            continue
        value = _value(raw)
        if value is not None:
            label = TYPE_INPUT_LABELS.get((driver_type or "", key), INPUT_LABELS[key])
            parts.append(f"{label}: {value}")
    return "; ".join(parts)


def _driver_fact(fact_id: str, driver: Driver, quotes: Mapping[UUID, str]) -> Fact:
    label = LABELS.get(driver.type, driver.type.replace("_", " ").capitalize())
    when = driver.occurred_at.date().isoformat()
    if driver.basis == "evidence":
        known = [quotes[e] for e in driver.evidence_ids if e in quotes]
        count = len(driver.evidence_ids)
        text = f"{label} ({count} quoted message{'s' if count != 1 else ''}, latest {when})."
        return Fact(
            id=fact_id,
            text=text,
            basis="evidence",
            evidence_ids=list(driver.evidence_ids),
            quotes=known,
        )
    detail = inputs_text(driver.inputs, driver.type)
    text = f"{label}" + (f" ({detail})." if detail else ".")
    return Fact(id=fact_id, text=text, basis="metadata", inputs=dict(driver.inputs or {}))


def render_facts(assessment: Assessment, quotes: Mapping[UUID, str]) -> list[Fact]:
    """Facts in a fixed order: drivers as the assessment ordered them (f1, f2, …), then the
    state and its caveats (c1, …). Deterministic for equal input."""
    facts = [
        _driver_fact(f"f{i}", driver, quotes)
        for i, driver in enumerate(assessment.drivers, start=1)
    ]
    since = assessment.state_since.isoformat()
    facts.append(
        Fact(
            id="s1",
            text=(
                f"Relationship state: {assessment.risk_state} since {since}"
                + (
                    f" (before: {assessment.previous_risk_state})"
                    if assessment.previous_risk_state
                    and assessment.previous_risk_state != assessment.risk_state
                    else ""
                )
                + f"; expansion: {assessment.expansion_state}; evidence strength: "
                f"{assessment.strength}."
            ),
            basis="metadata",
            inputs={
                "risk_state": assessment.risk_state,
                "state_since": since,
                "expansion_state": assessment.expansion_state,
                "strength": assessment.strength,
            },
        )
    )
    for i, caveat in enumerate(assessment.caveats, start=1):
        facts.append(
            Fact(
                id=f"c{i}",
                text=f"Caveat: {caveat.detail}",
                basis="metadata",
                inputs={"code": caveat.code},
            )
        )
    return facts
