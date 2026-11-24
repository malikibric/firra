"""Replay-derived signals, latent needs and v2 item fields, scoped to one eval workspace."""

import json
from collections.abc import Iterator
from datetime import date
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from firra.db.evals import EvalRepo, EvalRunRepo
from firra.db.pool import create_pool_for
from firra.db.types import WorkspaceId
from tests.conftest import local_settings

pytestmark = pytest.mark.integration

KEYS = {"Halden Co": "halden"}
SERVICES = {"Attribution & analytics": "attribution"}
INSERT_ASSESSMENT = """
insert into public.account_assessment (
  workspace_id, client_id, as_of, run_kind, replay_run_id, assessment_version, rule_version,
  risk_state, state_since, expansion_state, strength, drivers, caveats, data, play)
values (%s, %s, %s, 'replay', %s, 'a1', 'r1', 'stable', %s, 'none', 'weak', %s, '[]', '{}',
        'monitor')
"""


def _driver(kind: str, evidence: list[UUID]) -> dict[str, object]:
    return {
        "type": kind,
        "family": "delivery",
        "severity": "medium",
        "counted_severity": "medium",
        "basis": "evidence",
        "dedupe_key": kind,
        "occurred_at": "2026-09-01T00:00:00+00:00",
        "inputs": {},
        "evidence_ids": [str(e) for e in evidence],
        "primary": True,
    }


@pytest.fixture
def worlds() -> Iterator[tuple[UUID, UUID, UUID, UUID, UUID]]:
    """Two workspaces with a same-named client each; only the first has the rows under test."""
    first, second = uuid4(), uuid4()
    evidence = [uuid4(), uuid4(), uuid4()]
    url = local_settings().database_url.get_secret_value()
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        runs: list[UUID] = []
        for index, workspace in enumerate((first, second)):
            conn.execute(
                "insert into public.workspace (id, name) values (%s, %s)", (workspace, f"W{index}")
            )
            client = conn.execute(
                "insert into public.client (workspace_id, name, policy) "
                "values (%s, 'Halden Co', 'full') returning id",
                (workspace,),
            ).fetchone()
            service = conn.execute(
                "insert into public.workspace_service (workspace_id, name, description, keywords) "
                "values (%s, 'Attribution & analytics', 'd', '{attribution}') returning id",
                (workspace,),
            ).fetchone()
            assert client and service
            connection = conn.execute(
                "insert into public.connection (workspace_id, provider, mode, account_label) "
                "values (%s, 'email_upload', 'upload', 'fixture') returning id",
                (workspace,),
            ).fetchone()
            assert connection
            record = conn.execute(
                "insert into public.source_record (workspace_id, connection_id, provider, "
                "external_id, kind, client_id, policy_at_ingest, occurred_at, direction, "
                "body_clean) values (%s, %s, 'email_upload', 'm1', 'email', %s, 'full', now(), "
                "'in', 'We keep asking about attribution reports.') returning id",
                (workspace, connection["id"], client["id"]),
            ).fetchone()
            assert record
            proof = conn.execute(
                "insert into public.evidence (workspace_id, source_record_id, client_id, quote, "
                "locator) values (%s, %s, %s, 'asking about attribution reports', %s) "
                "returning id",
                (workspace, record["id"], client["id"], Jsonb({"start": 12, "end": 43})),
            ).fetchone()
            assert proof
            with conn.transaction():
                need = conn.execute(
                    "insert into public.latent_need (workspace_id, client_id, topic, "
                    "workspace_service_id, mention_count, thread_count, first_seen_at, "
                    "last_seen_at, cluster_key, rule_version) "
                    "values (%s, %s, 'multi-touch reporting', %s, %s, 3, now(), now(), 'k', 'r1') "
                    "returning id",
                    (workspace, client["id"], service["id"], 4 + index),
                ).fetchone()
                assert need
                conn.execute(
                    "insert into public.latent_need_evidence (workspace_id, latent_need_id, "
                    "evidence_id, client_id) values (%s, %s, %s, %s)",
                    (workspace, need["id"], proof["id"], client["id"]),
                )
            row = conn.execute(
                "insert into public.replay_run (workspace_id, window_start, window_end, "
                "step_days) values (%s, '2026-04-01', '2026-09-30', 7) returning id",
                (workspace,),
            ).fetchone()
            assert row
            runs.append(row["id"])
            weeks = [
                (date(2026, 9, 9), [_driver("delivery_complaint", evidence[:2])]),
                (
                    date(2026, 9, 16),
                    [
                        _driver("delivery_complaint", evidence[1:]),
                        _driver("tone_decline", evidence[2:]),
                    ],
                ),
                (date(2026, 9, 23), []),
            ]
            if index == 0:
                for as_of, drivers in weeks:
                    conn.execute(
                        INSERT_ASSESSMENT,
                        (workspace, client["id"], as_of, row["id"], as_of, Jsonb(drivers)),
                    )
        try:
            yield first, second, runs[0], runs[1], evidence[0]
        finally:
            for workspace in (first, second):
                conn.execute("delete from public.workspace where id = %s", (workspace,))


def test_signals_are_the_union_over_the_replay_weeks_with_their_evidence(
    worlds: tuple[UUID, UUID, UUID, UUID, UUID],
) -> None:
    first, second, run_first, run_second, first_evidence = worlds
    with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
        signals = EvalRepo(pool, WorkspaceId(first)).collect_signals(run_first, KEYS)
        other = EvalRepo(pool, WorkspaceId(second)).collect_signals(run_second, KEYS)
        crossed = EvalRepo(pool, WorkspaceId(second)).collect_signals(run_first, KEYS)
        unknown = EvalRepo(pool, WorkspaceId(first)).collect_signals(run_first, {})
    assert [(s.client, s.type) for s in signals] == [
        ("halden", "delivery_complaint"),
        ("halden", "tone_decline"),
    ]
    delivery, tone = signals
    assert len(delivery.evidence_ids) == 3 and first_evidence in delivery.evidence_ids
    assert len(tone.evidence_ids) == 1
    assert other == [] and crossed == [] and unknown == []


def test_needs_carry_counts_and_the_spec_key_of_the_matched_service(
    worlds: tuple[UUID, UUID, UUID, UUID, UUID],
) -> None:
    first, second, *_ = worlds
    with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
        needs = EvalRepo(pool, WorkspaceId(first)).collect_needs(KEYS, SERVICES)
        other = EvalRepo(pool, WorkspaceId(second)).collect_needs(KEYS, SERVICES)
        unmapped = EvalRepo(pool, WorkspaceId(first)).collect_needs(KEYS, {})
    assert [(n.client, n.mentions, n.threads, n.service) for n in needs] == [
        ("halden", 4, 3, "attribution")
    ]
    assert [(n.mentions, n.service) for n in other] == [(5, "attribution")]
    assert unmapped[0].service is None


@pytest.fixture
def card_world() -> Iterator[tuple[UUID, UUID]]:
    workspace = uuid4()
    url = local_settings().database_url.get_secret_value()
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        conn.execute("insert into public.workspace (id, name) values (%s, 'W')", (workspace,))
        client = conn.execute(
            "insert into public.client (workspace_id, name, policy) "
            "values (%s, 'Halden Co', 'full') returning id",
            (workspace,),
        ).fetchone()
        connection = conn.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label) "
            "values (%s, 'email_upload', 'upload', 'fixture') returning id",
            (workspace,),
        ).fetchone()
        assert client and connection
        record = conn.execute(
            "insert into public.source_record (workspace_id, connection_id, provider, "
            "external_id, kind, client_id, policy_at_ingest, occurred_at, direction, body_clean) "
            "values (%s, %s, 'email_upload', 'm1', 'email', %s, 'full', now(), 'in', "
            "'The delivery was late again this month.') returning id",
            (workspace, connection["id"], client["id"]),
        ).fetchone()
        assert record
        proof = conn.execute(
            "insert into public.evidence (workspace_id, source_record_id, client_id, quote, "
            "locator) values (%s, %s, %s, 'The delivery was late again', %s) returning id",
            (workspace, record["id"], client["id"], Jsonb({"start": 0, "end": 27})),
        ).fetchone()
        run = conn.execute(
            "insert into public.replay_run (workspace_id, window_start, window_end, step_days) "
            "values (%s, '2026-04-01', '2026-09-30', 7) returning id",
            (workspace,),
        ).fetchone()
        assert proof and run
        assessment = conn.execute(
            INSERT_ASSESSMENT + " returning id",
            (
                workspace,
                client["id"],
                date(2026, 9, 28),
                run["id"],
                date(2026, 9, 28),
                Jsonb(
                    [
                        _driver("delivery_complaint", [proof["id"]]),
                        {**_driver("tone_decline", []), "primary": False},
                    ]
                ),
            ),
        ).fetchone()
        assert assessment
        facts = [
            {
                "id": "f1",
                "text": "Three complaints about delivery in 30 days.",
                "basis": "evidence",
                "evidence_ids": [str(proof["id"])],
            }
        ]
        for week, interpretation in (
            (date(2026, 9, 21), "Delivery complaints are recurring."),
            (date(2026, 9, 28), "Seven complaints are recurring."),
        ):
            with conn.transaction():
                card = conn.execute(
                    "insert into public.decision_card (workspace_id, client_id, week, kind, "
                    "title, body, locale, score, priority, inputs, model, prompt_version, "
                    "dedupe_key, assessment_id, play, facts, interpretation, recommendation, "
                    "strength, caveats) values (%s, %s, %s, 'risk', 'Delivery is slipping', "
                    "'b', 'en', 5, 1, %s, 'm', 'cards.v1', %s, %s, 'fix_first', %s, %s, "
                    "'Call them this week.', 'strong', '[]') returning id",
                    (
                        workspace,
                        client["id"],
                        week,
                        Jsonb({"fact_ids": ["f1"]}),
                        f"k{week}",
                        assessment["id"],
                        Jsonb(facts),
                        interpretation,
                    ),
                ).fetchone()
                assert card
                conn.execute(
                    "insert into public.decision_evidence (workspace_id, client_id, "
                    "decision_card_id, evidence_id) values (%s, %s, %s, %s)",
                    (workspace, client["id"], card["id"], proof["id"]),
                )
        try:
            yield workspace, proof["id"]
        finally:
            conn.execute("delete from public.workspace where id = %s", (workspace,))


def test_stored_cards_are_rechecked_against_their_own_facts(card_world: tuple[UUID, UUID]) -> None:
    workspace, _ = card_world
    with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
        cards = EvalRepo(pool, WorkspaceId(workspace)).collect_cards(KEYS)
        none = EvalRepo(pool, WorkspaceId(uuid4())).collect_cards(KEYS)
    assert [(c.client, c.kind, c.source_signal_types) for c in cards] == [
        ("halden", "risk", ["delivery_complaint"])
    ] * 2
    # The second card says "Seven", a number word no fact contains; the first is clean.
    assert [c.integrity_violations for c in cards] == [[], ["number_word:interpretation"]]
    assert none == []
    with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
        unmapped = EvalRepo(pool, WorkspaceId(workspace)).collect_cards({})
    # A card for a client truth does not know is kept, under a sentinel, for the scorer to count.
    assert [c.client for c in unmapped] == ["unknown_client"] * 2


def test_a_fact_id_with_model_text_never_reaches_the_violation_classes(
    card_world: tuple[UUID, UUID],
) -> None:
    workspace, _ = card_world
    url = local_settings().database_url.get_secret_value()
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        conn.execute(
            "update public.decision_card set inputs = %s where workspace_id = %s",
            (Jsonb({"fact_ids": ["Confidential phrase in a fact id"]}), workspace),
        )
    with create_pool_for(url) as pool:
        cards = EvalRepo(pool, WorkspaceId(workspace)).collect_cards(KEYS)
    assert all("unknown_fact" in c.integrity_violations for c in cards)
    assert "Confidential" not in json.dumps([c.model_dump(mode="json") for c in cards])


def test_live_assessments_after_the_evaluated_day_are_discarded_and_only_those(
    worlds: tuple[UUID, UUID, UUID, UUID, UUID],
) -> None:
    first, second, *_ = worlds
    url = local_settings().database_url.get_secret_value()
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        for workspace in (first, second):
            client = conn.execute(
                "select id from public.client where workspace_id = %s", (workspace,)
            ).fetchone()
            assert client
            for day in (date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)):
                conn.execute(
                    "insert into public.account_assessment (workspace_id, client_id, as_of, "
                    "run_kind, assessment_version, rule_version, risk_state, state_since, "
                    "expansion_state, strength, drivers, caveats, data, play) values "
                    "(%s, %s, %s, 'live', 'a1', 'r1', 'stable', %s, 'none', 'weak', '[]', '[]', "
                    "'{}', 'monitor')",
                    (workspace, client["id"], day, day),
                )
        with create_pool_for(url) as pool:
            removed = EvalRunRepo(pool, WorkspaceId(first)).discard_live_assessments_after(
                date(2026, 9, 30)
            )
        left = {
            row["workspace_id"]: [d.isoformat() for d in row["days"]]
            for row in conn.execute(
                "select workspace_id, array_agg(as_of order by as_of) as days "
                "from public.account_assessment where run_kind = 'live' and workspace_id = any(%s) "
                "group by workspace_id",
                ([first, second],),
            ).fetchall()
        }
    assert removed == 1
    assert left[first] == ["2026-09-29", "2026-09-30"]
    # Another workspace's rows are never touched, whatever their dates.
    assert left[second] == ["2026-09-29", "2026-09-30", "2026-10-01"]


def test_load_facts_carries_the_client_service_tags_and_each_need_its_service_name(
    worlds: tuple[UUID, UUID, UUID, UUID, UUID],
) -> None:
    from datetime import UTC, datetime

    from firra.db.signals import SignalRepo

    first, *_ = worlds
    url = local_settings().database_url.get_secret_value()
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        client = conn.execute(
            "update public.client set service_tags = %s where workspace_id = %s returning id",
            (["Attribution & analytics", "Paid media"], first),
        ).fetchone()
    assert client
    with create_pool_for(url) as pool:
        facts = SignalRepo(pool).load_facts(
            workspace_id=WorkspaceId(first),
            client_id=client["id"],
            ref=datetime.now(UTC),
        )
    assert facts is not None
    assert facts.service_tags == ("Attribution & analytics", "Paid media")
    assert [n.service_name for n in facts.needs] == ["Attribution & analytics"]
    assert facts.buys(facts.needs[0].service_name)


def test_stalled_queued_jobs_are_returned_once_and_only_while_nothing_runs(
    worlds: tuple[UUID, UUID, UUID, UUID, UUID],
) -> None:
    first, second, *_ = worlds
    url = local_settings().database_url.get_secret_value()
    insert = (
        "insert into public.job (workspace_id, type, status, idempotency_key, payload, updated_at) "
        "values (%s, %s, %s, %s, %s, now() - interval '10 minutes') returning id"
    )
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        stuck = conn.execute(
            insert, (first, "synth.cards", "queued", "k-stuck", Jsonb({"week": "2026-09-28"}))
        ).fetchone()
        conn.execute(insert, (second, "synth.cards", "queued", "k-other", Jsonb({})))
        conn.execute(
            "insert into public.job (workspace_id, type, status, idempotency_key, payload) "
            "values (%s, 'memory.rebuild', 'queued', 'k-fresh', '{}')",
            (first,),
        )
        assert stuck
        with create_pool_for(url) as pool:
            repo = EvalRunRepo(pool, WorkspaceId(first))
            got = repo.stalled_jobs(older_than_seconds=60)
            # only this workspace's old queued job: not the other workspace's, not the fresh one
            assert [(e.type, e.job_id, e.payload) for e in got] == [
                ("synth.cards", stuck["id"], {"week": "2026-09-28"})
            ]
            assert repo.stalled_jobs(older_than_seconds=60) == []  # bumped: once per interval
            conn.execute(
                "update public.job set updated_at = now() - interval '10 minutes' "
                "where workspace_id = %s and idempotency_key = 'k-stuck'",
                (first,),
            )
            conn.execute(
                "insert into public.job (workspace_id, type, status, idempotency_key, payload) "
                "values (%s, 'process.records', 'running', 'k-running', '{}')",
                (first,),
            )
            assert repo.stalled_jobs(older_than_seconds=60) == []  # a job is running: wait
