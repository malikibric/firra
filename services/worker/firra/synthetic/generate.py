"""Writes message bodies for the planned threads with FIRRA_MODEL_SYNTHDATA (P2 D17).

This is the only part of the dataset that costs money, and it runs once: the output is committed
(synthetic/northbeam/corpus/). One call per thread; every ``include`` sentence must come back
verbatim, otherwise the thread is asked again once and then the run stops. Metadata-only clients
get deterministic filler bodies (ingest never stores them and no model reads them).

Bodies are then made realistic deterministically: senders get a signature block with their role
(client senders skip it on some replies, as from a phone) and some replies carry the quoted
previous message, so the corpus exercises cleaning and the pipeline can learn role titles from
signatures (D8).
"""

import json
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from firra.db.llm_usage import WorkspaceBudget
from firra.db.types import WorkspaceId
from firra.llm.prices import cost_usd
from firra.llm.prompts import Prompt
from firra.llm.service import Llm
from firra.synthetic.plan import MEETING_MINUTES, Contact, Plan, PlannedMeeting, PlannedThread
from firra.synthetic.spec import Spec
from firra_contracts import CalendarEventRecord, Message

PROMPT_VERSION = "synthdata.v1"
# Generation is not tied to a workspace; usage is capped in memory (no database involved).
GENERATION_WORKSPACE = WorkspaceId(UUID(int=0))
MIN_WORDS, MAX_WORDS = 15, 260
# Rough token sizes for the cost estimate shown before anything is spent.
SYSTEM_TOKENS, TOKENS_PER_PLANNED, TOKENS_PER_BODY = 700, 150, 230


class ThreadBodies(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bodies: list[str] = Field(min_length=1, max_length=8)


class GenerationError(Exception):
    """A thread could not be generated with its required sentences."""


@dataclass
class CappedUsage:
    """In-memory usage store with a hard cap (FIRRA_EVAL_BUDGET_USD) for offline generation."""

    cap_usd: Decimal
    spent: Decimal = Decimal(0)
    calls: int = 0
    tokens: list[tuple[int, int]] = field(default_factory=list)

    def record(
        self, *, input_tokens: int, output_tokens: int, cost_usd: Decimal, **_: object
    ) -> None:
        self.calls += 1
        self.spent += cost_usd
        self.tokens.append((input_tokens, output_tokens))

    def spent_since(self, *, workspace_id: WorkspaceId, since: datetime) -> Decimal:
        return self.spent

    def budget(self, *, workspace_id: WorkspaceId) -> WorkspaceBudget:
        return WorkspaceBudget(self.cap_usd, datetime(2000, 1, 1, tzinfo=UTC))

    def note_budget_threshold(
        self, *, workspace_id: WorkspaceId, month: date, threshold: int
    ) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class Corpus:
    messages: list[Message]
    events: list[CalendarEventRecord]


def estimate_cost_usd(plan: Plan, model: str, on: date) -> Decimal:
    """Upper-bound style estimate of one full generation run (one attempt per thread)."""
    input_tokens = output_tokens = 0
    for thread in plan.threads:
        if thread.metadata_only:
            continue
        input_tokens += SYSTEM_TOKENS + TOKENS_PER_PLANNED * len(thread.messages)
        output_tokens += TOKENS_PER_BODY * len(thread.messages)
    return cost_usd(model, input_tokens=input_tokens, output_tokens=output_tokens, on=on)


def _who(contact: Contact, spec: Spec) -> str:
    company = spec.firm.name if contact.side == "firm" else "the client"
    return f"{contact.name} ({contact.role}, {company})"


def thread_request(spec: Spec, thread: PlannedThread) -> str:
    client = spec.client(thread.client)
    payload = {
        "agency": {"name": spec.firm.name, "business": "US B2B marketing agency"},
        "client": {"name": client.name, "business": client.industry},
        "subject": thread.subject,
        "messages": [
            {
                "n": n,
                "from": _who(m.sender, spec),
                "to": [_who(c, spec) for c in m.to],
                "cc": [_who(c, spec) for c in m.cc],
                "date": m.at.date().isoformat(),
                "brief": m.brief,
                "include": list(m.include),
            }
            for n, m in enumerate(thread.messages, start=1)
        ],
    }
    return json.dumps(payload, indent=2)


def check_bodies(thread: PlannedThread, bodies: list[str]) -> str | None:
    """Why the answer is unusable, or None."""
    if len(bodies) != len(thread.messages):
        return "wrong number of bodies"
    for message, body in zip(thread.messages, bodies, strict=True):
        for sentence in message.include:
            if body.count(sentence) != 1:
                return "required sentence missing or repeated"
        if not MIN_WORDS <= len(body.split()) <= MAX_WORDS:
            return "body length out of range"
        if "@" in body or "http" in body:
            return "body contains an address or link"
    return None


def filler_bodies(thread: PlannedThread) -> list[str]:
    """Deterministic bodies for metadata-only clients (never stored, never analyzed)."""
    rng = random.Random(thread.key)
    bodies = []
    for message in thread.messages:
        first = message.to[0].name.split()[0]
        detail = " ".join(
            rng.choice(("draft", "notes", "timing", "outline", "numbers"))
            for _ in range(rng.randint(8, 30))
        )
        bodies.append(
            f"Hi {first},\n\nQuick note about {thread.topic}: {detail}.\n\n"
            f"Thanks,\n{message.sender.name.split()[0]}"
        )
    return bodies


def decorate(spec: Spec, thread: PlannedThread, bodies: list[str]) -> list[str]:
    """Adds signatures and, on some replies, the quoted previous message."""
    rng = random.Random(f"{spec.seed}:{thread.key}:decorate")
    client_name = next(c.name for c in spec.clients if c.key == thread.client)
    out: list[str] = []
    for i, (message, body) in enumerate(zip(thread.messages, bodies, strict=True)):
        text = body.strip()
        sender = message.sender
        if sender.side == "firm":
            text += f"\n\n-- \n{sender.name}\n{sender.role}, {spec.firm.name}"
        elif i == 0 or rng.random() < 0.7:
            text += f"\n\n-- \n{sender.name}\n{sender.role}, {client_name}"
        if i > 0 and rng.random() < 0.5:
            previous = thread.messages[i - 1]
            stamp = previous.at.strftime("%a, %b %d, %Y at %I:%M %p")
            quoted = "\n".join(f"> {line}".rstrip() for line in bodies[i - 1].strip().split("\n"))
            text += (
                f"\n\nOn {stamp} {previous.sender.name} <{previous.sender.email}> wrote:\n{quoted}"
            )
        out.append(text)
    return out


def _participant(contact: Contact) -> dict[str, str]:
    return {"email": contact.email, "name": contact.name}


def to_messages(thread: PlannedThread, bodies: list[str]) -> list[Message]:
    messages: list[Message] = []
    for i, (planned, body) in enumerate(zip(thread.messages, bodies, strict=True)):
        messages.append(
            Message.model_validate(
                {
                    "provider": "email_upload",
                    "external_id": planned.external_id,
                    "thread_key": thread.key,
                    "occurred_at": planned.at,
                    "sender": _participant(planned.sender),
                    "to": [_participant(c) for c in planned.to],
                    "cc": [_participant(c) for c in planned.cc],
                    "bcc": [],
                    "subject": thread.subject if i == 0 else f"Re: {thread.subject}",
                    "body_text": body,
                    "message_id_header": planned.external_id,
                    "in_reply_to": thread.messages[i - 1].external_id if i > 0 else None,
                    "deep_link": None,
                }
            )
        )
    return messages


def to_event(meeting: PlannedMeeting) -> CalendarEventRecord:
    return CalendarEventRecord.model_validate(
        {
            "provider": "google_calendar",
            "external_id": meeting.external_id,
            "starts_at": meeting.starts_at,
            "ends_at": meeting.starts_at + timedelta(minutes=MEETING_MINUTES),
            "status": meeting.status,
            "rescheduled": meeting.rescheduled,
            "organizer": _participant(meeting.organizer),
            "attendees": [_participant(c) for c in meeting.attendees],
            "title": meeting.title,
            "deep_link": None,
        }
    )


def generate_corpus(spec: Spec, plan: Plan, llm: Llm, prompt: Prompt) -> Corpus:
    messages: list[Message] = []
    for thread in plan.threads:
        if thread.metadata_only:
            bodies = filler_bodies(thread)
        else:
            problem: str | None = "not generated"
            for _ in (1, 2):
                answer = llm.structured(
                    workspace_id=GENERATION_WORKSPACE,
                    job_id=None,
                    purpose="synthdata",
                    prompt_version=prompt.version,
                    system=prompt.text,
                    user=thread_request(spec, thread),
                    output=ThreadBodies,
                )
                bodies = answer.value.bodies
                problem = check_bodies(thread, bodies)
                if problem is None:
                    break
            if problem is not None:
                raise GenerationError(f"{thread.key}: {problem}")
        messages.extend(to_messages(thread, decorate(spec, thread, bodies)))
    return Corpus(messages=messages, events=[to_event(m) for m in plan.meetings])
