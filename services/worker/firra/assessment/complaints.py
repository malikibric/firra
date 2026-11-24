"""Find client complaints without a later resolution in their thread."""

from datetime import datetime

from firra.signals.facts import ClientFacts, ExtractionFact
from firra.signals.rules.base import in_window


def unresolved_complaints(facts: ClientFacts, *, within_days: int) -> list[ExtractionFact]:
    if within_days <= 0:
        raise ValueError("within_days must be positive")

    records = {r.id: r for r in facts.records if r.occurred_at <= facts.ref}
    client_ids = {
        r.id for r in records.values() if r.direction == "in" and r.author_side == "client"
    }
    done_evidence = {
        eid
        for commitment in facts.commitments
        if commitment.owner_side == "firm" and commitment.status == "done"
        for eid in commitment.evidence_ids
    }
    latest_resolution: dict[str, datetime] = {}
    candidates = []
    for item in facts.extractions:
        record = records.get(item.record_id)
        if record is None:
            continue
        if (
            item.kind == "complaint"
            and record.id in client_ids
            and in_window(item.occurred_at, facts.ref, newer_than_days=within_days)
        ):
            candidates.append(item)
        if record.thread_key is None:
            continue
        positive = record.id in client_ids and (
            item.kind == "praise"
            or (item.kind == "tone" and item.payload.get("tone") == "positive")
        )
        completed = item.kind == "commitment" and bool(
            done_evidence.intersection(item.evidence_ids)
        )
        if positive or completed:
            latest_resolution[record.thread_key] = max(
                latest_resolution.get(record.thread_key, record.occurred_at), record.occurred_at
            )

    unresolved = []
    for complaint in candidates:
        thread = records[complaint.record_id].thread_key
        resolution = latest_resolution.get(thread) if thread is not None else None
        if resolution is None or resolution <= complaint.occurred_at:
            unresolved.append(complaint)
    return sorted(unresolved, key=lambda item: (facts.ref - item.occurred_at, str(item.id)))
