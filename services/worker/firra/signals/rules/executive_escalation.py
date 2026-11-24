"""A client complaint involves a decision maker directly or a newly seen one on its thread."""

from datetime import timedelta

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, evidence_ids, in_window, newest


class ExecutiveEscalationConfig(RuleConfig):
    window_days: int = Field(gt=0)
    new_person_days: int = Field(gt=0)
    max_evidence: int = Field(gt=0)
    severity: Severity
    dedupe_key: str


def evaluate(facts: ClientFacts, cfg: ExecutiveEscalationConfig) -> list[SignalDraft]:
    messages = facts.client_messages()
    by_record = {r.id: r for r in messages}
    decision_makers = {p.id: p for p in facts.people if p.is_decision_maker}
    matched = []
    for item in facts.extractions:
        complaint = by_record.get(item.record_id)
        if (
            item.kind != "complaint"
            or complaint is None
            or not item.evidence_ids
            or not in_window(item.occurred_at, facts.ref, newer_than_days=cfg.window_days)
        ):
            continue
        if complaint.author_person_id in decision_makers:
            matched.append(item)
            continue
        if complaint.thread_key is None:
            continue
        for message in messages:
            if message.author_person_id is None:
                continue
            person = decision_makers.get(message.author_person_id)
            if (
                person is not None
                and person.first_seen_at is not None
                and person.first_seen_at <= facts.ref
                and message.thread_key == complaint.thread_key
                and message.occurred_at >= complaint.occurred_at
                and in_window(
                    person.first_seen_at,
                    complaint.occurred_at + timedelta(days=cfg.new_person_days),
                    newer_than_days=2 * cfg.new_person_days,
                )
            ):
                matched.append(item)
                break
    if not matched:
        return []
    return [
        SignalDraft(
            family=cfg.family,
            type="executive_escalation",
            severity=cfg.severity,
            occurred_at=newest(matched)[0].occurred_at,
            basis="evidence",
            dedupe_key=cfg.dedupe_key,
            evidence_ids=evidence_ids(matched, cfg.max_evidence),
        )
    ]


SPEC = RuleSpec(ExecutiveEscalationConfig, evaluate)
