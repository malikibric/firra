"""Deterministic expansion of the spec into threads, messages and meetings (no model calls).

Planted scenario messages keep their dates and wording requirements. Routine threads fill each
client's ``message_budget``: random topics, business-hour timestamps, alternating sides and reply
gaps, all from a per-client seeded RNG, so the same spec always gives the same plan. Volume and
reply-latency changes in the spec shape the routine traffic (reduced engagement, latency drift);
meeting changes turn recurring check-ins into cancellations or reschedules.
"""

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from firra.synthetic.spec import Client, ExpectedItem, Person, Spec

Side = Literal["client", "firm"]
MEETING_MINUTES = 30
UPCOMING_MEETING_DAYS = 14


@dataclass(frozen=True, slots=True)
class Contact:
    key: str
    name: str
    email: str
    role: str
    side: Side


@dataclass(frozen=True, slots=True)
class PlannedMessage:
    external_id: str
    at: datetime
    sender: Contact
    to: tuple[Contact, ...]
    cc: tuple[Contact, ...]
    brief: str
    include: tuple[str, ...] = ()
    expect: tuple[ExpectedItem, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannedThread:
    key: str
    client: str
    subject: str
    topic: str
    scenario: str | None
    injection: bool
    metadata_only: bool
    messages: tuple[PlannedMessage, ...]


@dataclass(frozen=True, slots=True)
class PlannedMeeting:
    external_id: str
    client: str
    starts_at: datetime
    status: Literal["confirmed", "cancelled"]
    rescheduled: bool
    organizer: Contact
    attendees: tuple[Contact, ...]
    title: str


@dataclass(frozen=True, slots=True)
class Plan:
    threads: tuple[PlannedThread, ...]
    meetings: tuple[PlannedMeeting, ...]

    @property
    def message_count(self) -> int:
        return sum(len(t.messages) for t in self.threads)


def _contact(person: Person, domain: str, side: Side) -> Contact:
    return Contact(person.key, person.name, f"{person.key}@{domain}", person.role, side)


class _People:
    def __init__(self, spec: Spec, client: Client) -> None:
        self.client = client
        self.firm = {p.key: _contact(p, spec.firm.domain, "firm") for p in spec.firm.people}
        self.clients = {p.key: _contact(p, client.domain, "client") for p in client.people}
        self.persons = {p.key: p for p in client.people}

    def get(self, key: str) -> Contact:
        return self.firm.get(key) or self.clients[key]

    def client_side_on(self, day: date) -> list[Person]:
        return [p for p in self.client.people if p.active_on(day)]

    def writers_on(self, day: date) -> list[Person]:
        """Client people who write routine messages on ``day`` (writers_after narrows them)."""
        active = self.client_side_on(day)
        change = self.client.writers_after
        if change is None or day < change.date:
            return active
        return [p for p in active if p.key in change.keys]


def _message_id(thread_key: str, index: int, domain: str) -> str:
    return f"<{thread_key}.{index}@synthetic.{domain}>"


def _routine_brief(side: Side, topic: str, reply: bool) -> str:
    opening = "Reply in this thread" if reply else "Start a thread"
    if side == "client":
        return (
            f"{opening} as the client about {topic}: share a status detail or ask a practical "
            "question. Neutral and factual: no complaints, no praise, no new requests beyond this "
            "topic, no budget or contract talk, no changes of people, no promises with dates."
        )
    return (
        f"{opening} as the agency about {topic}: report progress or answer the question. Neutral "
        "and factual: no promises with dates, no pricing talk, no changes of people."
    )


def _volume_weight(client: Client, day: date) -> float:
    weight = 1.0
    change = client.volume_after
    if change is not None and day >= change.date:
        weight *= change.factor
    for window in client.volume_windows:
        if window.start <= day <= window.end:
            weight *= window.factor
    return weight


def _window(spec: Spec, client: Client) -> tuple[date, date]:
    return max(spec.start, client.contract_start), spec.end_of(client)


def _business_days(first: date, last: date) -> list[date]:
    days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    return [d for d in days if d.weekday() < 5]


def _reply_gap(client: Client, side: Side, day: date, rng: random.Random) -> timedelta:
    if side == "client":
        slow = client.latency_after
        if slow is not None and day >= slow.date:
            return timedelta(hours=rng.uniform(slow.min_hours, slow.max_hours))
        return timedelta(hours=rng.uniform(1.5, 8))
    return timedelta(hours=rng.uniform(0.5, 6))


def _planted_threads(spec: Spec, client: Client) -> list[PlannedThread]:
    people = _People(spec, client)
    threads: list[PlannedThread] = []
    for scenario in spec.scenarios:
        if scenario.client != client.key:
            continue
        for n, thread in enumerate(scenario.threads, start=1):
            key = f"{scenario.key}-{n}"
            messages = tuple(
                PlannedMessage(
                    external_id=_message_id(key, i, spec.firm.domain),
                    at=m.at,
                    sender=people.get(m.sender),
                    to=tuple(people.get(k) for k in m.to),
                    cc=tuple(people.get(k) for k in m.cc),
                    brief=m.brief,
                    include=tuple(m.include),
                    expect=tuple(m.expect),
                )
                for i, m in enumerate(thread.messages)
            )
            threads.append(
                PlannedThread(
                    key=key,
                    client=client.key,
                    subject=thread.subject,
                    topic=thread.subject,
                    scenario=scenario.key,
                    injection=thread.injection,
                    metadata_only=client.policy == "metadata_only",
                    messages=messages,
                )
            )
    return threads


def _spread_days(
    days: list[date], weights: list[float], count: int, rng: random.Random
) -> list[date]:
    """``count`` days spread by weight (systematic sampling): volume changes always show."""
    total = sum(weights)
    offset = rng.random()
    picked: list[date] = []
    cumulative = 0.0
    index = 0
    for n in range(count):
        target = (n + offset) * total / count
        while index < len(days) - 1 and cumulative + weights[index] < target:
            cumulative += weights[index]
            index += 1
        picked.append(days[index])
    return picked


def _routine_threads(spec: Spec, client: Client, budget: int) -> list[PlannedThread]:
    rng = random.Random(f"{spec.seed}:{client.key}")
    people = _People(spec, client)
    first, last = _window(spec, client)
    days = _business_days(first, last)
    weights = [_volume_weight(client, d) for d in days]
    end = datetime.combine(last, time(23, 59), tzinfo=UTC)
    threads: list[PlannedThread] = []

    def thread_on(day: date, size: int) -> PlannedThread | None:
        active = people.writers_on(day)
        if not active:
            return None
        champions = [p for p in active if p.champion]
        client_person = people.clients[
            (champions[0] if champions and rng.random() < 0.7 else rng.choice(active)).key
        ]
        firm_person = people.firm[rng.choice(client.firm_team)]
        others = [people.clients[p.key] for p in active if p.key != client_person.key]
        cc = (rng.choice(others),) if others and rng.random() < 0.2 else ()
        topic = rng.choice(client.routine_topics)
        key = f"{client.key}-r{len(threads) + 1:03d}"
        side: Side = "client" if rng.random() < 0.5 else "firm"
        at = datetime.combine(day, time(rng.randint(13, 20), rng.randint(0, 59)), tzinfo=UTC)
        messages: list[PlannedMessage] = []
        for i in range(size):
            if i > 0:
                at += _reply_gap(client, side, at.date(), rng)
                if at > end or not people.client_side_on(at.date()):
                    break
            sender, recipient = (
                (client_person, firm_person) if side == "client" else (firm_person, client_person)
            )
            messages.append(
                PlannedMessage(
                    external_id=_message_id(key, i, spec.firm.domain),
                    at=at,
                    sender=sender,
                    to=(recipient,),
                    cc=cc,
                    brief=_routine_brief(side, topic, reply=i > 0),
                )
            )
            side = "firm" if side == "client" else "client"
        return PlannedThread(
            key=key,
            client=client.key,
            subject=topic[:1].upper() + topic[1:],
            topic=topic,
            scenario=None,
            injection=False,
            metadata_only=client.policy == "metadata_only",
            messages=tuple(messages),
        )

    sizes: list[int] = []
    while sum(sizes) < budget:
        sizes.append(min(budget - sum(sizes), rng.choice((1, 2, 2, 3, 3, 4))))
    remaining = budget
    for day, size in zip(_spread_days(days, weights, len(sizes), rng), sizes, strict=True):
        thread = thread_on(day, size)
        if thread is not None:
            threads.append(thread)
            remaining -= len(thread.messages)
    # Threads cut short (window end, a departed contact) are topped up with single messages.
    attempts = 0
    while remaining > 0:
        attempts += 1
        if attempts > budget * 20:  # pragma: no cover - spec validation keeps windows wide
            raise RuntimeError(f"{client.key}: cannot place routine messages")
        thread = thread_on(rng.choices(days, weights)[0], 1)
        if thread is not None:
            threads.append(thread)
            remaining -= 1
    return threads


def _meetings(spec: Spec, client: Client) -> list[PlannedMeeting]:
    people = _People(spec, client)
    first, _ = _window(spec, client)
    # Churned clients meet until they leave; active ones also have the next check-ins booked.
    stop = client.churned_at or spec.as_of + timedelta(days=UPCOMING_MEETING_DAYS)
    changes = next(
        (s.meetings for s in spec.scenarios if s.client == client.key and s.meetings), None
    )
    pending = list(changes.pattern) if changes else []
    organizer = people.firm[client.firm_team[0]]
    meetings: list[PlannedMeeting] = []
    day = first + timedelta(days=7)
    while day <= stop:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        active = people.client_side_on(day)
        champion = next((p for p in active if p.champion), active[0] if active else None)
        if champion is not None:
            starts = datetime.combine(day, time(15, 0), tzinfo=UTC)
            status: Literal["confirmed", "cancelled"] = "confirmed"
            rescheduled = False
            if changes and pending and changes.after <= day <= spec.as_of:
                change = pending.pop(0)
                if change == "cancelled":
                    status = "cancelled"
                else:
                    rescheduled = True
                    starts += timedelta(days=2)
            meetings.append(
                PlannedMeeting(
                    external_id=f"{client.key}-meeting-{len(meetings) + 1:03d}",
                    client=client.key,
                    starts_at=starts,
                    status=status,
                    rescheduled=rescheduled,
                    organizer=organizer,
                    attendees=(organizer, people.clients[champion.key]),
                    title=f"{client.name} and {spec.firm.name} check-in",
                )
            )
        day += timedelta(days=client.meeting_every_days)
    return meetings


def build_plan(spec: Spec) -> Plan:
    threads: list[PlannedThread] = []
    meetings: list[PlannedMeeting] = []
    for client in spec.clients:
        planted = _planted_threads(spec, client)
        routine_budget = client.message_budget - sum(len(t.messages) for t in planted)
        threads.extend(planted)
        threads.extend(_routine_threads(spec, client, routine_budget))
        meetings.extend(_meetings(spec, client))
    threads.sort(key=lambda t: (t.messages[0].at, t.key))
    return Plan(threads=tuple(threads), meetings=tuple(meetings))
