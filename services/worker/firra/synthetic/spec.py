"""Scenario spec of the synthetic dataset (P2 D17), validated on load.

The spec is the single source of the dataset: people, clients, routine topics and planted
scenarios with their expected outcomes. Cross-references (person keys, services, quotes that
must be among the message's ``include`` sentences, dates inside the timeline) are checked here so
the generator and the evals can rely on them.
"""

from datetime import date, datetime
from pathlib import Path
from typing import Literal, Self

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator

from firra.extract.schema import ComplaintSubtype, ExtractionKind, Side, StakeholderChange, Tone

SPEC_PATH = Path(__file__).resolve().parents[2] / "synthetic" / "northbeam" / "spec.yaml"

# Signal types the evals check (names match services/worker/config/signals.yaml, step 9).
SignalType = Literal[
    "delivery_complaint",
    "repeated_complaint",
    "executive_escalation",
    "tone_decline",
    "champion_departure",
    "new_senior_stakeholder",
    "reduced_engagement",
    "champion_latency_drift",
    "in_house_risk",
    "renewal_window",
    "goal_result_mismatch",
    "late_commitment",
    "meeting_cancellations",
    "first_90_days_no_result",
    # P2b (the design notes)
    "client_initiation_drop",
    "client_reply_latency_drift",
    "participant_narrowing",
    "decision_maker_silence",
    "meeting_attendance_drop",
    "scope_cut_talk",
    "expansion_need",
    "client_initiative",
    "budget_growth",
]
# Rules that count email volume: a revoked mailbox would fake them (p2b D4 coverage guard).
USES_VOLUME: frozenset[str] = frozenset(
    {
        "reduced_engagement",
        "client_initiation_drop",
        "client_reply_latency_drift",
        "participant_narrowing",
        "decision_maker_silence",
        "champion_latency_drift",
    }
)
AdviceKind = Literal["fix_first", "opportunity", "renewal_pricing", "maintain"]
RiskState = Literal["stable", "watch", "elevated", "high"]
ExpansionState = Literal["none", "hypothesis", "strong"]
Play = Literal[
    "fix_first",
    "stakeholder_remap",
    "renewal_prep",
    "founder_review_call",
    "expansion_conversation",
    "monitor",
]
Caveat = Literal[
    "coverage_changed",
    "low_data",
    "holiday_period",
    "recent_positive",
    "stakeholder_handover",
    "metadata_only",
    "participant_coverage",
]
# Extraction v2 (p2b D10) adds `initiative`; defined here so the dataset can describe it before the
# extraction schema changes (T18).
TruthKind = ExtractionKind | Literal["initiative"]
BudgetDirection = Literal["grow", "cut", "renewal", "neutral"]
InitiativeType = Literal["market", "product", "team", "hire", "rebrand", "funding", "other"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Person(_Model):
    key: str = Field(pattern=r"^[a-z]+\.[a-z]+$")
    name: str
    role: str
    champion: bool = False
    decision_maker: bool = False
    joined: date | None = None
    left: date | None = None
    # Firm people only: their connected mailbox stops syncing on this date (coverage control).
    mailbox_revoked_at: date | None = None

    def active_on(self, day: date) -> bool:
        return (self.joined is None or day >= self.joined) and (
            self.left is None or day <= self.left
        )


class Service(_Model):
    key: str
    name: str
    description: str
    keywords: list[str]


class Firm(_Model):
    name: str
    domain: str
    people: list[Person]
    services: list[Service]


class VolumeChange(_Model):
    date: date
    factor: float = Field(gt=0, le=1)


class VolumeWindow(_Model):
    """Routine traffic weight between two dates (inclusive); factors multiply."""

    start: date
    end: date
    factor: float = Field(gt=0, le=10)


class WritersChange(_Model):
    """From ``date`` on, only these client-side people write (fewer stakeholders involved)."""

    date: date
    keys: list[str] = Field(min_length=1)


class LatencyChange(_Model):
    date: date
    min_hours: int = Field(gt=0)
    max_hours: int = Field(gt=0)


class Client(_Model):
    key: str
    name: str
    domain: str
    industry: str
    status: Literal["active", "churned"] = "active"
    policy: Literal["full", "metadata_only"] = "full"
    churned_at: date | None = None
    mrr: int
    contract_start: date
    renewal_date: date
    notice_period_days: int
    services: list[str]
    message_budget: int = Field(gt=0)
    firm_team: list[str] = Field(min_length=1)
    people: list[Person] = Field(min_length=1)
    routine_topics: list[str] = Field(min_length=1)
    meeting_every_days: int = 14
    volume_after: VolumeChange | None = None
    volume_windows: list[VolumeWindow] = []
    writers_after: WritersChange | None = None
    latency_after: LatencyChange | None = None

    @model_validator(mode="after")
    def _churn_date(self) -> Self:
        if (self.status == "churned") != (self.churned_at is not None):
            raise ValueError(f"{self.key}: churned_at is required exactly for churned clients")
        keys = {p.key for p in self.people}
        if self.writers_after and not set(self.writers_after.keys) <= keys:
            raise ValueError(f"{self.key}: writers_after names an unknown person")
        return self


class ExpectedItem(_Model):
    kind: TruthKind
    quote: str
    subtype: ComplaintSubtype | None = None
    tone: Tone | None = None
    owner_side: Side | None = None
    due_date: date | None = None
    change: StakeholderChange | None = None
    budget_direction: BudgetDirection | None = None
    initiative_type: InitiativeType | None = None

    @model_validator(mode="after")
    def _v2_fields(self) -> Self:
        if (self.kind == "renewal_or_budget_talk") != (self.budget_direction is not None):
            raise ValueError(
                f"budget_direction is required exactly for budget talk: {self.quote!r}"
            )
        if (self.kind == "initiative") != (self.initiative_type is not None):
            raise ValueError(f"initiative_type is required exactly for initiatives: {self.quote!r}")
        return self


class PlantedMessage(_Model):
    at: datetime
    sender: str
    to: list[str] = Field(min_length=1)
    cc: list[str] = []
    brief: str
    include: list[str] = []
    expect: list[ExpectedItem] = []

    @model_validator(mode="after")
    def _quotes_are_included(self) -> Self:
        for item in self.expect:
            if item.quote not in self.include:
                raise ValueError(f"expected quote is not an include sentence: {item.quote!r}")
        return self


class PlantedThread(_Model):
    subject: str
    injection: bool = False
    messages: list[PlantedMessage] = Field(min_length=1)


class LatentNeed(_Model):
    topic: str
    mentions: int = Field(ge=3)
    threads: int = Field(ge=2)
    service: str | None


class ExpectedAssessment(_Model):
    """The account state Firra must show at the dataset's as-of date (p2b D6, D9)."""

    risk: RiskState
    expansion: ExpansionState
    play: Play
    by: date | None = None  # earliest weekly as-of at which ``risk`` must be reached


class Expectation(_Model):
    signals: list[SignalType]
    # Fire on the raw data but must be suppressed by the coverage guard (never stored).
    suppressed_signals: list[SignalType] = []
    latent_needs: list[LatentNeed] = []
    no_latent_needs: list[str] = []
    advise: AdviceKind | None = None
    assessment: ExpectedAssessment | None = None
    max_risk: RiskState | None = None  # highest state allowed at any weekly as-of in 180 days
    caveats: list[Caveat] = []
    detect_by: date | None = None  # churned clients: elevated or high by this date


class MeetingChanges(_Model):
    after: date
    pattern: list[Literal["cancelled", "rescheduled"]] = Field(min_length=1)


class Scenario(_Model):
    key: str
    client: str
    description: str
    expect: Expectation
    threads: list[PlantedThread] = []
    meetings: MeetingChanges | None = None


class Spec(_Model):
    version: str
    seed: int
    start: date
    as_of: date
    total_messages: int
    firm: Firm
    clients: list[Client]
    scenarios: list[Scenario]

    def client(self, key: str) -> Client:
        return next(c for c in self.clients if c.key == key)

    def end_of(self, client: Client) -> date:
        """Last day with traffic: the churn date or the as-of date."""
        return client.churned_at or self.as_of

    @model_validator(mode="after")
    def _cross_references(self) -> Self:
        firm_keys = {p.key for p in self.firm.people}
        services = {s.key for s in self.firm.services}
        clients = {c.key: c for c in self.clients}
        if len(clients) != len(self.clients):
            raise ValueError("duplicate client keys")
        if sum(c.message_budget for c in self.clients) != self.total_messages:
            raise ValueError("client message budgets must add up to total_messages")
        for c in self.clients:
            if not set(c.firm_team) <= firm_keys or not set(c.services) <= services:
                raise ValueError(f"{c.key}: unknown firm person or service")
        for s in self.scenarios:
            client = clients.get(s.client)
            if client is None:
                raise ValueError(f"{s.key}: unknown client {s.client}")
            people = firm_keys | {p.key for p in client.people}
            planted = 0
            for thread in s.threads:
                for m in thread.messages:
                    planted += 1
                    if not {m.sender, *m.to, *m.cc} <= people:
                        raise ValueError(f"{s.key}: unknown person in {thread.subject!r}")
                    day = m.at.date()
                    if not max(self.start, client.contract_start) <= day <= self.end_of(client):
                        raise ValueError(f"{s.key}: message outside the client's timeline")
            for need in s.expect.latent_needs:
                if need.service is not None and need.service not in services:
                    raise ValueError(f"{s.key}: unknown service {need.service}")
            if planted > client.message_budget:
                raise ValueError(f"{s.key}: more planted messages than the client's budget")
            detect_by = s.expect.detect_by
            if detect_by is not None and (
                client.churned_at is None or (client.churned_at - detect_by).days < 30
            ):
                raise ValueError(f"{s.key}: detect_by needs a churned client and 30 days of lead")
        for c in self.clients:
            if sum(1 for s in self.scenarios if s.client == c.key and s.expect.assessment) > 1:
                raise ValueError(f"{c.key}: more than one expected assessment")
        revoked = {p.key for p in self.firm.people if p.mailbox_revoked_at}
        for s in self.scenarios:
            team = set(clients[s.client].firm_team)
            if team & revoked and USES_VOLUME & set(s.expect.signals):
                raise ValueError(f"{s.key}: a revoked mailbox would fake its volume signals")
        return self


def load_spec(path: Path = SPEC_PATH) -> Spec:
    return Spec.model_validate(yaml.safe_load(path.read_text()))
