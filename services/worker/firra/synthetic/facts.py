"""Signal facts built straight from the plan, as if ingest and extraction were perfect.

No database and no model: records come from the planned messages, people and flags from the spec,
meetings from the planned calendar, and extractions from the expected items (each with its own
evidence id). Used to prove that the spec and the rules agree before any paid run — a failing paid
eval then points at extraction, not at the dataset (the design notes).
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid5

from firra.signals.facts import (
    ClientFacts,
    CommitmentFact,
    CoverageFact,
    EventFact,
    ExtractionFact,
    NeedFact,
    PersonFact,
    RecordFact,
)
from firra.synthetic.plan import Plan, PlannedMessage
from firra.synthetic.spec import Client, ExpectedItem, Spec

NAMESPACE = UUID("5f0e8c1a-4b2d-4c6e-9a7f-000000000017")


def uid(*parts: str) -> UUID:
    return uuid5(NAMESPACE, ":".join(parts))


def end_of(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59), tzinfo=UTC)


def _revoked(spec: Spec) -> dict[str, date]:
    return {p.key: p.mailbox_revoked_at for p in spec.firm.people if p.mailbox_revoked_at}


def mailbox_key(spec: Spec, firm_keys: list[str], day: date) -> str | None:
    """The mailbox a message lands in: its first firm participant still connected on ``day``."""
    revoked = _revoked(spec)
    return next((k for k in firm_keys if k not in revoked or day < revoked[k]), None)


def _firm_keys(message: PlannedMessage) -> list[str]:
    return [c.key for c in (message.sender, *message.to, *message.cc) if c.side == "firm"]


def ingested(spec: Spec, message: PlannedMessage) -> bool:
    return mailbox_key(spec, _firm_keys(message), message.at.date()) is not None


def _payload(item: ExpectedItem, speaker: str) -> dict[str, Any]:
    fields = item.model_dump(mode="json", exclude_none=True, exclude={"kind", "quote"})
    return {"speaker": speaker, "text": item.quote, **fields}


def client_facts(spec: Spec, plan: Plan, client: Client, ref: datetime) -> ClientFacts:
    """Facts of ``client`` at ``ref``: nothing after it, nothing a revoked mailbox missed."""
    threads = [t for t in plan.threads if t.client == client.key]
    catalog = {service.key: service.name for service in spec.firm.services}
    people = {p.key: p for p in client.people}
    first_seen: dict[str, datetime] = {}
    records: list[RecordFact] = []
    extractions: list[ExtractionFact] = []
    commitments: list[CommitmentFact] = []
    full = client.policy == "full"
    for thread in threads:
        for message in thread.messages:
            for contact in (message.sender, *message.to, *message.cc):
                if contact.side == "client":
                    seen = first_seen.get(contact.key)
                    first_seen[contact.key] = min(seen, message.at) if seen else message.at
            if message.at > ref or not ingested(spec, message):
                continue
            record_id = uid("record", message.external_id)
            from_client = message.sender.side == "client"
            author = uid("person", client.key, message.sender.key) if from_client else None
            records.append(
                RecordFact(
                    id=record_id,
                    thread_key=thread.key,
                    occurred_at=message.at,
                    direction="in" if from_client else "out",
                    author_person_id=author,
                    author_side="client" if from_client else "firm",
                    processed=full,
                    connection_id=uid(
                        "mailbox", mailbox_key(spec, _firm_keys(message), message.at.date()) or "-"
                    ),
                )
            )
            if not full:
                continue
            for n, item in enumerate(message.expect):
                evidence = (uid("evidence", message.external_id, str(n)),)
                if item.kind == "commitment":
                    commitments.append(
                        CommitmentFact(
                            id=uid("commitment", message.external_id, str(n)),
                            owner_side=item.owner_side or "firm",
                            due_date=item.due_date,
                            status="open",
                            evidence_ids=evidence,
                        )
                    )
                extractions.append(
                    ExtractionFact(
                        id=uid("extraction", message.external_id, str(n)),
                        record_id=record_id,
                        kind=item.kind,
                        payload=_payload(item, message.sender.email),
                        occurred_at=message.at,
                        author_person_id=author,
                        evidence_ids=evidence,
                    )
                )
    persons = tuple(
        PersonFact(
            id=uid("person", client.key, p.key),
            name=p.name,
            is_decision_maker=p.decision_maker,
            is_champion=p.champion,
            departed_at=end_of(p.left) if p.left and end_of(p.left) <= ref else None,
            first_seen_at=first_seen.get(p.key),
        )
        for p in people.values()
        if p.key in first_seen and first_seen[p.key] <= ref
    )
    events = tuple(
        EventFact(
            starts_at=m.starts_at,
            status="cancelled"
            if m.status == "cancelled"
            else "rescheduled"
            if m.rescheduled
            else "held",
            attendee_count=len(m.attendees),
        )
        for m in plan.meetings
        if m.client == client.key and m.starts_at <= ref
    )
    baseline_ids = {
        r.connection_id
        for r in records
        if ref - timedelta(days=120) < r.occurred_at <= ref - timedelta(days=30)
    }
    coverage = tuple(
        CoverageFact(
            connection_id=uid("mailbox", person.key),
            status="revoked" if person.mailbox_revoked_at else "active",
            synced_until=end_of(person.mailbox_revoked_at) if person.mailbox_revoked_at else None,
        )
        for person in sorted(spec.firm.people, key=lambda p: p.key)
        if uid("mailbox", person.key) in baseline_ids
    )
    needs: list[NeedFact] = []
    if full:
        for scenario in spec.scenarios:
            if scenario.client != client.key or not scenario.expect.latent_needs:
                continue
            planted = {
                uid("record", message.external_id)
                for thread in threads
                if thread.scenario == scenario.key
                for message in thread.messages
            }
            mentions = sorted(
                (x for x in extractions if x.kind == "request" and x.record_id in planted),
                key=lambda x: (x.occurred_at, str(x.id)),
            )
            for need in scenario.expect.latent_needs:
                # The spec plants this need's request cluster; no semantic model is needed.
                if len(mentions) < need.mentions:
                    continue
                needs.append(
                    NeedFact(
                        id=uid("need", client.key, scenario.key, need.topic),
                        workspace_service_id=uid("service", need.service) if need.service else None,
                        service_name=catalog[need.service] if need.service else None,
                        status="open",
                        last_seen_at=mentions[-1].occurred_at,
                        mention_count=len(mentions),
                        evidence_ids=tuple(e for x in mentions for e in x.evidence_ids),
                    )
                )
    return ClientFacts(
        client_id=uid("client", client.key),
        status=client.status,
        policy=client.policy,
        contract_start=client.contract_start,
        renewal_date=client.renewal_date,
        notice_period_days=client.notice_period_days,
        ref=ref,
        service_tags=tuple(catalog[key] for key in client.services),
        people=persons,
        records=tuple(records),
        extractions=tuple(extractions),
        commitments=tuple(commitments),
        events=events,
        coverage=coverage,
        needs=tuple(needs),
    )


def weekly_refs(spec: Spec, client: Client, *, days: int = 180) -> list[datetime]:
    """Weekly reference times ending at the client's last day (churn date or as-of)."""
    last = spec.end_of(client)
    return [end_of(last - timedelta(days=7 * n)) for n in range(days // 7, -1, -1)]
