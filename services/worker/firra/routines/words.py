"""Fixed card wording; no client text enters the renderer."""

from dataclasses import dataclass
from typing import Any

from firra.routines.model import Decision

WORDS = {
    "risk_rose_elevated": (
        "Client risk rose",
        "Review the evidence and address the relationship risk.",
    ),
    "risk_rose_watch": ("Client needs a closer look", "Review the change and agree a next step."),
    "risk_rose_other": ("Client state changed", "Review the latest relationship state."),
    "risk_fell": ("Client risk fell", "Review the improvement and continue monitoring."),
    "expansion_strong": (
        "Expansion opportunity strengthened",
        "Review the opportunity with the client.",
    ),
    "expansion_hypothesis": (
        "Expansion hypothesis changed",
        "Review the hypothesis before acting.",
    ),
    "signal_high": ("A significant signal opened", "Review the evidence and recommended play."),
    "signal_opened": ("A relationship signal opened", "Review the new signal."),
    "signal_resolved": ("A relationship signal resolved", "Review the resolution in context."),
    "commitment_late_at_risk": (
        "Our commitment is late on an at-risk client",
        "Resolve the overdue commitment and update the client.",
    ),
    "commitment_late": (
        "Our commitment is late",
        "Confirm ownership and agree a revised delivery date.",
    ),
    "client_commitment_late": (
        "A client commitment is late",
        "Review the commitment before following up.",
    ),
    "notice_deadline_close": (
        "Renewal notice deadline is close",
        "Review the relationship before the notice deadline.",
    ),
    "notice_deadline_near": (
        "Renewal notice deadline is approaching",
        "Prepare for the notice deadline.",
    ),
}


@dataclass(frozen=True)
class CardText:
    title: str
    body: str
    interpretation: str
    recommendation: str
    facts: list[dict[str, Any]]


def render(decision: Decision) -> CardText:
    title, recommendation = WORDS[decision.reason_code]
    basis = "evidence" if decision.evidence_ids else "metadata"
    return CardText(
        title,
        f"{title}. {recommendation}",
        f"{title}.",
        recommendation,
        [{"id": decision.reason_code, "text": f"{title}.", "basis": basis}],
    )
