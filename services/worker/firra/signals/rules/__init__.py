"""Rule registry: one module per rule, each exporting ``SPEC``.

A rule is a pure function of ``ClientFacts`` and its own validated config.
"""

from firra.signals.rules import (
    budget_growth,
    champion_departure,
    champion_latency_drift,
    client_initiation_drop,
    client_initiative,
    client_reply_latency_drift,
    decision_maker_silence,
    delivery_complaint,
    executive_escalation,
    expansion_need,
    first_90_days_no_result,
    goal_result_mismatch,
    in_house_risk,
    late_commitment,
    meeting_attendance_drop,
    meeting_cancellations,
    new_senior_stakeholder,
    participant_narrowing,
    reduced_engagement,
    renewal_window,
    repeated_complaint,
    scope_cut_talk,
    tone_decline,
)
from firra.signals.rules.base import RuleSpec

RULES: dict[str, RuleSpec] = {
    "client_initiation_drop": client_initiation_drop.SPEC,
    "client_reply_latency_drift": client_reply_latency_drift.SPEC,
    "participant_narrowing": participant_narrowing.SPEC,
    "decision_maker_silence": decision_maker_silence.SPEC,
    "meeting_attendance_drop": meeting_attendance_drop.SPEC,
    "scope_cut_talk": scope_cut_talk.SPEC,
    "expansion_need": expansion_need.SPEC,
    "client_initiative": client_initiative.SPEC,
    "budget_growth": budget_growth.SPEC,
    "tone_decline": tone_decline.SPEC,
    "reduced_engagement": reduced_engagement.SPEC,
    "delivery_complaint": delivery_complaint.SPEC,
    "repeated_complaint": repeated_complaint.SPEC,
    "executive_escalation": executive_escalation.SPEC,
    "champion_departure": champion_departure.SPEC,
    "champion_latency_drift": champion_latency_drift.SPEC,
    "new_senior_stakeholder": new_senior_stakeholder.SPEC,
    "meeting_cancellations": meeting_cancellations.SPEC,
    "late_commitment": late_commitment.SPEC,
    "goal_result_mismatch": goal_result_mismatch.SPEC,
    "first_90_days_no_result": first_90_days_no_result.SPEC,
    "in_house_risk": in_house_risk.SPEC,
    "renewal_window": renewal_window.SPEC,
}
