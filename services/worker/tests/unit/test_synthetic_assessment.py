"""Northbeam's dated assessment expectations agree with perfect-extraction facts."""

from datetime import date, timedelta

import pytest

from firra.assessment.config import risk_rank
from firra.assessment.summary import Step, detection, flicker
from firra.synthetic.assessment import weekly_assessments
from firra.synthetic.facts import client_facts, end_of, uid
from firra.synthetic.plan import build_plan
from firra.synthetic.spec import Client, load_spec

SPEC = load_spec()
PLAN = build_plan(SPEC)


@pytest.mark.parametrize("client", SPEC.clients, ids=lambda c: c.key)
def test_weekly_assessments_match_expectations(client: Client) -> None:
    series = weekly_assessments(SPEC, PLAN, client)
    steps = [Step(a.as_of, a.risk_state) for a in series]
    last = series[-1]
    for scenario in SPEC.scenarios:
        if scenario.client != client.key:
            continue
        expect = scenario.expect
        if client.status == "active" and expect.assessment:
            expected = expect.assessment
            assert (last.risk_state, last.expansion_state, last.play) == (
                expected.risk,
                expected.expansion,
                expected.play,
            )
            if expected.by:
                reached = next(
                    (
                        a.as_of
                        for a in series
                        if risk_rank(a.risk_state) >= risk_rank(expected.risk)
                    ),
                    None,
                )
                assert reached is not None and reached <= expected.by
        if expect.max_risk:
            assert all(risk_rank(a.risk_state) <= risk_rank(expect.max_risk) for a in series)
        assert set(expect.caveats) <= {c.code for c in last.caveats}
        if client.status == "churned" and expect.detect_by:
            detected = detection(steps)
            assert detected is not None and detected <= expect.detect_by
    assert flicker(steps) == 0


def test_coverage_uses_only_baseline_connections_and_current_sync_state() -> None:
    client = SPEC.client("halden")
    facts = client_facts(SPEC, PLAN, client, end_of(SPEC.as_of))
    baseline = {
        r.connection_id
        for r in facts.records
        if facts.ref - timedelta(days=120) < r.occurred_at <= facts.ref - timedelta(days=30)
    }
    assert {c.connection_id for c in facts.coverage} == baseline
    revoked = next(c for c in facts.coverage if c.connection_id == uid("mailbox", "gus.meyer"))
    assert revoked.status == "revoked"
    assert revoked.synced_until == end_of(date(2026, 9, 5))
    assert all(
        c.status == "active" and c.synced_until is None for c in facts.coverage if c != revoked
    )


@pytest.mark.parametrize(
    "key, last_day, count, service",
    [
        ("halden", date(2026, 9, 8), 4, "attribution"),
        ("kestrel", date(2026, 9, 1), 3, "web_cro"),
    ],
)
def test_planted_need_window_evidence_and_service_match(
    key: str, last_day: date, count: int, service: str | None
) -> None:
    client = SPEC.client(key)
    before = client_facts(SPEC, PLAN, client, end_of(last_day - timedelta(days=1)))
    assert before.needs == ()
    facts = client_facts(SPEC, PLAN, client, end_of(last_day))
    (need,) = facts.needs
    requests = [x for x in facts.extractions if x.kind == "request"]
    assert need.status == "open"
    assert need.mention_count == count
    assert need.last_seen_at == max(x.occurred_at for x in requests)
    assert set(need.evidence_ids) == {e for x in requests for e in x.evidence_ids}
    assert need.workspace_service_id == (uid("service", service) if service else None)
    final = client_facts(SPEC, PLAN, client, end_of(SPEC.as_of))
    assert final.needs == facts.needs


def test_below_threshold_and_metadata_only_clients_have_no_needs() -> None:
    for key in ("quillon", "vantage", "meridian"):
        assert client_facts(SPEC, PLAN, SPEC.client(key), end_of(SPEC.as_of)).needs == ()
