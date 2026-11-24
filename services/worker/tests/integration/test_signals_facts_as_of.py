"""Leak-free facts (P2b D5): facts as of a date never see what happened after it."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.db.pool import create_pool_for
from firra.db.signals import SignalRepo
from firra.db.types import WorkspaceId
from firra.signals.facts import ClientFacts
from firra.signals.model import SignalDraft
from tests.conftest import local_settings

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]

REF = datetime(2026, 9, 30, 12, tzinfo=UTC)
QUOTE = "We are late again with this report"


@pytest.fixture
def db() -> Iterator[Db]:
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as conn:
        yield conn


@pytest.fixture
def repo() -> Iterator[SignalRepo]:
    with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
        yield SignalRepo(pool)


def _id(row: dict[str, Any] | None) -> UUID:
    assert row is not None
    return UUID(str(row["id"]))


class World:
    """One workspace, one full client, three connections, three people, all dated around ``t``."""

    def __init__(self, db: Db, t: datetime) -> None:
        self.db = db
        self.t = t
        self.ws = _id(
            db.execute(
                "insert into public.workspace (name) values ('facts-as-of') returning id"
            ).fetchone()
        )
        self.client = _id(
            db.execute(
                "insert into public.client (workspace_id, name, policy) "
                "values (%s, 'acme', 'full') returning id",
                (self.ws,),
            ).fetchone()
        )
        self.mail = self._connection("gmail", "user", "active", None)
        self.revoked = self._connection("gmail", "user", "revoked", t - timedelta(days=10))
        self.upload = self._connection("email_upload", "upload", "active", None)
        self.ana = self._person("Ana", t - timedelta(days=200))
        self.ben = self._person("Ben", t - timedelta(days=200))
        self.cara = self._person("Cara", t - timedelta(days=150))
        self._n = 0

    def _connection(self, provider: str, mode: str, status: str, synced: datetime | None) -> UUID:
        label = f"{provider}-{status}-{mode}"
        return _id(
            self.db.execute(
                "insert into public.connection (workspace_id, provider, mode, account_label, "
                "external_account_id, status, last_synced_at) "
                "values (%s, %s, %s, %s, %s, %s, %s) returning id",
                (self.ws, provider, mode, label, label, status, synced),
            ).fetchone()
        )

    def _person(self, name: str, first_seen: datetime) -> UUID:
        return _id(
            self.db.execute(
                "insert into public.person (workspace_id, client_id, side, display_name, "
                "first_seen_at) values (%s, %s, 'client', %s, %s) returning id",
                (self.ws, self.client, name, first_seen),
            ).fetchone()
        )

    def record(self, person: UUID, at: datetime, connection: UUID | None = None) -> UUID:
        self._n += 1
        return _id(
            self.db.execute(
                "insert into public.source_record (workspace_id, connection_id, provider, "
                "external_id, kind, client_id, policy_at_ingest, thread_key, occurred_at, "
                "author_person_id, direction, participants, body_clean, processing_state) "
                "values (%s, %s, 'gmail', %s, 'email', %s, 'full', %s, %s, %s, 'in', %s, %s, "
                "'done') returning id",
                (
                    self.ws,
                    connection or self.mail,
                    f"m{self._n}",
                    self.client,
                    f"t{self._n % 3}",
                    at,
                    person,
                    [person],
                    f"{QUOTE}.",
                ),
            ).fetchone()
        )

    def chat(self, person: UUID, at: datetime, thread: str | None = None) -> UUID:
        """A Slack message of the client in channel C1 (thread ``thread`` or the channel itself)."""
        self._n += 1
        slack = getattr(self, "slack", None) or self._connection("slack", "user", "active", None)
        self.slack = slack
        return _id(
            self.db.execute(
                "insert into public.source_record (workspace_id, connection_id, provider, "
                "external_id, kind, client_id, policy_at_ingest, thread_key, occurred_at, "
                "author_person_id, direction, participants, body_clean, processing_state, metadata)"
                " values (%s, %s, 'slack', %s, 'chat', %s, 'full', %s, %s, %s, 'in', %s, %s, "
                "'done', '{\"channel_id\": \"C1\"}') returning id",
                (self.ws, slack, f"c{self._n}", self.client, thread, at, person, [person], "Hi."),
            ).fetchone()
        )

    def owner(self, email: str) -> UUID:
        """A firm-side person whose email is the mailbox of the active gmail connection."""
        firm = _id(
            self.db.execute(
                "insert into public.person (workspace_id, side, display_name, first_seen_at) "
                "values (%s, 'firm', 'Olivia', %s) returning id",
                (self.ws, self.t - timedelta(days=300)),
            ).fetchone()
        )
        self.db.execute(
            "update public.connection set external_account_id = %s where id = %s",
            (email, self.mail),
        )
        self.db.execute(
            "insert into public.person_identity (workspace_id, person_id, kind, value) "
            "values (%s, %s, 'email', %s)",
            (self.ws, firm, email),
        )
        return firm

    def evidence(self, record: UUID) -> UUID:
        return _id(
            self.db.execute(
                "insert into public.evidence (workspace_id, source_record_id, client_id, quote, "
                'locator) values (%s, %s, %s, %s, \'{"start": 0, "end": 34}\') returning id',
                (self.ws, record, self.client, QUOTE),
            ).fetchone()
        )

    def tone(self, record: UUID) -> None:
        with self.db.transaction():
            extraction = _id(
                self.db.execute(
                    "insert into public.extraction (workspace_id, source_record_id, client_id, "
                    "kind, payload, model, prompt_version, processing_version, item_index) "
                    "values (%s, %s, %s, 'tone', '{\"tone\": \"negative\"}', 'm', 'extract.v2', "
                    "1, 0) returning id",
                    (self.ws, record, self.client),
                ).fetchone()
            )
            self.db.execute(
                "insert into public.extraction_evidence (workspace_id, extraction_id, "
                "evidence_id, client_id) values (%s, %s, %s, %s)",
                (self.ws, extraction, self.evidence(record), self.client),
            )

    def observation(
        self, person: UUID, at: datetime, source: str = "signature", decision: bool = True
    ) -> None:
        self.db.execute(
            "insert into public.person_observation (workspace_id, client_id, person_id, "
            "observed_at, source, role_title, is_decision_maker) "
            "values (%s, %s, %s, %s, %s, 'CMO', %s)",
            (self.ws, self.client, person, at, source, decision),
        )

    def commitment(
        self, key: str, stated: datetime, due: datetime | None, closed: datetime | None = None
    ) -> UUID:
        evidence = self.evidence(self.record(self.ana, stated))
        with self.db.transaction():
            commitment = _id(
                self.db.execute(
                    "insert into public.commitment (workspace_id, client_id, owner_side, text, "
                    "due_date, status, stated_at, dedupe_key, closed_at) "
                    "values (%s, %s, 'firm', 'Send the report.', %s, %s, %s, %s, %s) returning id",
                    (
                        self.ws,
                        self.client,
                        due.date() if due else None,
                        "done" if closed else "open",
                        stated,
                        key,
                        closed,
                    ),
                ).fetchone()
            )
            self.db.execute(
                "insert into public.commitment_evidence (workspace_id, commitment_id, "
                "evidence_id, client_id) values (%s, %s, %s, %s)",
                (self.ws, commitment, evidence, self.client),
            )
        return commitment

    def event(self, at: datetime, attendees: int) -> None:
        record = self.db.execute(
            "insert into public.source_record (workspace_id, connection_id, provider, "
            "external_id, kind, client_id, policy_at_ingest, occurred_at, direction, "
            "participants, processing_state) values (%s, %s, 'gmail', %s, 'calendar_event', %s, "
            "'full', %s, 'internal', '{}', 'not_applicable') returning id",
            (self.ws, self.mail, f"e{at.isoformat()}", self.client, at),
        ).fetchone()
        self.db.execute(
            "insert into public.calendar_event (workspace_id, client_id, starts_at, duration_min, "
            "status, attendee_count, source_record_id) values (%s, %s, %s, 30, 'held', %s, %s)",
            (self.ws, self.client, at, attendees, _id(record)),
        )

    def need(self, key: str, last_seen: datetime, status: str = "open") -> UUID:
        evidence = self.evidence(self.record(self.ana, last_seen))
        with self.db.transaction():
            return self._need(key, last_seen, status, evidence)

    def _need(self, key: str, last_seen: datetime, status: str, evidence: UUID) -> UUID:
        need = _id(
            self.db.execute(
                "insert into public.latent_need (workspace_id, client_id, mention_count, "
                "thread_count, first_seen_at, last_seen_at, status, cluster_key, rule_version) "
                "values (%s, %s, 3, 2, %s, %s, %s, %s, 'needs.v1') returning id",
                (self.ws, self.client, last_seen - timedelta(days=20), last_seen, status, key),
            ).fetchone()
        )
        self.db.execute(
            "insert into public.latent_need_evidence (workspace_id, latent_need_id, evidence_id, "
            "client_id) values (%s, %s, %s, %s)",
            (self.ws, need, evidence, self.client),
        )
        return need


def _world(db: Db, t: datetime) -> World:
    w = World(db, t)
    # Ana writes most in the champion window; Ben less. Ana signed as CMO before t.
    for day in (5, 12, 20, 33, 50, 70):
        w.tone(w.record(w.ana, t - timedelta(days=day)))
    for day in (8, 40, 60):
        w.record(w.ben, t - timedelta(days=day))
    w.observation(w.ana, t - timedelta(days=100))
    # A manual observation states a role; it counts from first_seen_at whatever its date.
    w.observation(w.cara, t + timedelta(days=30), source="manual")
    # Baseline coverage: the revoked mailbox and the upload carried records 31-120 days ago.
    w.record(w.ben, t - timedelta(days=45), connection=w.revoked)
    w.record(w.ben, t - timedelta(days=35), connection=w.upload)
    w.commitment("late", t - timedelta(days=20), t - timedelta(days=3))
    w.commitment("done", t - timedelta(days=30), None, closed=t - timedelta(days=10))
    w.commitment("closed-later", t - timedelta(days=30), None, closed=t + timedelta(days=5))
    w.event(t - timedelta(days=20), attendees=4)
    w.need("known", t - timedelta(days=10))
    w.need("dismissed", t - timedelta(days=10), status="dismissed")
    w.chat(w.ana, t - timedelta(days=2))
    w.chat(w.ben, t - timedelta(days=9), thread="1700000000.000100")
    return w


def _future(w: World) -> None:
    """Everything a later day adds: none of it may reach facts as of ``w.t``."""
    t = w.t
    for hours in range(1, 8):
        w.tone(w.record(w.ben, t + timedelta(hours=hours)))
    dan = w._person("Dan", t + timedelta(days=2))
    w.record(dan, t + timedelta(days=2))
    w.observation(w.ben, t + timedelta(days=2))
    w.observation(dan, t + timedelta(days=3), source="stakeholder_change")
    w.db.execute(
        "update public.commitment set status = 'done', closed_at = %s "
        "where workspace_id = %s and dedupe_key = 'late'",
        (t + timedelta(days=4), w.ws),
    )
    w.commitment("stated-later", t + timedelta(days=3), t + timedelta(days=9))
    w.db.execute(
        "update public.person set departed_at = %s where workspace_id = %s and id = %s",
        (t + timedelta(days=6), w.ws, w.ana),
    )
    w.event(t + timedelta(days=5), attendees=3)
    w.need("later", t + timedelta(days=7))
    w.chat(w.ben, t + timedelta(hours=3))
    w.chat(dan, t + timedelta(days=4), thread="1700000000.000100")


def _facts(repo: SignalRepo, w: World) -> ClientFacts:
    facts = repo.load_facts(workspace_id=WorkspaceId(w.ws), client_id=w.client, ref=w.t)
    assert facts is not None
    return facts


@pytest.mark.parametrize("days_back", [0, 40, 100])
def test_facts_do_not_change_when_later_data_arrives(
    db: Db, repo: SignalRepo, days_back: int
) -> None:
    w = _world(db, REF - timedelta(days=days_back))
    before = _facts(repo, w)
    assert before.records and before.extractions and before.commitments and before.needs
    _future(w)
    assert _facts(repo, w) == before


def test_people_roles_and_champion_are_computed_as_of_ref(db: Db, repo: SignalRepo) -> None:
    w = _world(db, REF)
    db.execute(
        "update public.person set is_champion = (id = %s), is_decision_maker = true, "
        "departed_at = %s where workspace_id = %s and id = %s",
        (w.ben, REF + timedelta(days=10), w.ws, w.ben),
    )
    people = {p.id: p for p in _facts(repo, w).people}
    assert people[w.ana].is_decision_maker and people[w.ana].is_champion
    assert people[w.cara].is_decision_maker  # manual observation dated after ref
    ben = people[w.ben]
    assert not ben.is_decision_maker and not ben.is_champion and ben.departed_at is None


def test_commitment_states_needs_events_and_coverage(db: Db, repo: SignalRepo) -> None:
    w = _world(db, REF)
    facts = _facts(repo, w)
    assert sorted(c.status for c in facts.commitments) == ["done", "late", "open"]
    assert [(n.status, len(n.evidence_ids)) for n in facts.needs] == [("open", 1)]
    assert [(e.status, e.attendee_count) for e in facts.events] == [("held", 4)]
    assert {r.connection_id for r in facts.records} == {w.mail, w.revoked, w.upload, w.slack}
    coverage = {c.connection_id: (c.status, c.synced_until) for c in facts.coverage}
    assert coverage == {
        w.mail: ("active", None),
        w.revoked: ("revoked", REF - timedelta(days=10)),
        w.upload: ("active", REF - timedelta(days=35)),
    }


def test_save_follows_the_policy_at_write_time(db: Db, repo: SignalRepo) -> None:
    w = _world(db, REF)
    evidence = w.evidence(w.record(w.ana, REF - timedelta(days=1)))
    drafts = [
        SignalDraft(
            type="tone_decline",
            severity="medium",
            occurred_at=REF,
            basis="evidence",
            dedupe_key="tone",
            evidence_ids=(evidence,),
        ),
        SignalDraft(
            type="reduced_engagement",
            severity="low",
            occurred_at=REF,
            basis="metadata",
            dedupe_key="volume",
            inputs={"ratio": 0.6},
        ),
    ]
    db.execute(
        "update public.client set policy = 'metadata_only' where workspace_id = %s and id = %s",
        (w.ws, w.client),
    )
    saved = repo.save(
        workspace_id=WorkspaceId(w.ws), client_id=w.client, drafts=drafts, rule_version="t"
    )
    assert saved.written == 1
    rows = db.execute("select type from public.signal where workspace_id = %s", (w.ws,)).fetchall()
    assert [r["type"] for r in rows] == ["reduced_engagement"]
    db.execute(
        "update public.client set policy = 'excluded' where workspace_id = %s and id = %s",
        (w.ws, w.client),
    )
    skipped = repo.save(
        workspace_id=WorkspaceId(w.ws), client_id=w.client, drafts=drafts, rule_version="t"
    )
    assert (skipped.written, skipped.resolved) == (0, 0)


def test_chat_records_timezone_and_owner_are_loaded(db: Db, repo: SignalRepo) -> None:
    w = _world(db, REF)
    db.execute("update public.workspace set timezone = 'Europe/Berlin' where id = %s", (w.ws,))
    olivia = w.owner("olivia@example.test")
    facts = _facts(repo, w)
    chats = [r for r in facts.records if r.source == "chat"]
    assert len(chats) == 2 and {r.source for r in facts.records} == {"email", "chat"}
    # chat records are keyed by channel and thread so replies match inside their conversation
    assert {r.thread_key for r in chats} == {"C1/", "C1/1700000000.000100"}
    assert facts.timezone == "Europe/Berlin"
    assert facts.owner_person_ids == {olivia}
