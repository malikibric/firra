from dataclasses import replace
from datetime import date, timedelta
from functools import partial
from uuid import UUID

import pytest

from firra.assessment.model import Assessment
from firra.routines.config import load_config
from firra.routines.eval import CardObs, ClientSeries, score_routines, score_suppression_probe

DAY = date(2026, 10, 5)
score = partial(score_routines, config=load_config())


def row(
    day: date, risk: str = "elevated", previous: str | None = "stable", expansion: str = "none"
) -> Assessment:
    return Assessment(day, risk, previous, day, expansion, "strong", (), (), "monitor", {})  # type: ignore[arg-type]


def world() -> tuple[ClientSeries, CardObs]:
    client = ClientSeries(
        "c",
        [row(DAY - timedelta(days=1), "stable", None), row(DAY)],
        churned_at=DAY + timedelta(days=40),
    )
    card = CardObs("c", DAY, "morning_scan", "state_change", "risk_rose_elevated", "important", 2)
    return client, card


@pytest.mark.parametrize("case", ["pass", "missing", "extra", "reason"])
def test_exactness(case: str) -> None:
    client, card = world()
    cards = (
        []
        if case == "missing"
        else [replace(card, reason_code="risk_fell") if case == "reason" else card]
    )
    if case == "extra":
        cards.append(replace(card, day=DAY + timedelta(days=1)))
    result = score([client], cards, [DAY, DAY + timedelta(days=1)])
    assert result.gates["exactness"] == (case == "pass")
    assert result.metrics["false_negatives"] == int(case == "missing")
    assert result.metrics["false_positives"] == int(case == "extra")


@pytest.mark.parametrize("case", ["pass", "four", "dropped", "unmarked"])
def test_budget_including_dropped_demoted_item(case: str) -> None:
    client, card = world()
    clients = [replace(client, key=str(i)) for i in range(4)]
    cards = [replace(card, client=str(i)) for i in range(3)]
    if case != "dropped":
        cards.append(
            replace(
                card,
                client="3",
                importance="important" if case == "four" else "attention",
                demoted_reason="budget" if case == "pass" else None,
            )
        )
    result = score(clients, cards, [DAY])
    assert result.gates["budget"] == (case == "pass")
    assert result.metrics["worst_week_important"] == (4 if case == "four" else 3)


@pytest.mark.parametrize("rank", [None, 2, 3])
def test_suppression_and_worse_risk_escape(rank: int | None) -> None:
    client, card = world()
    cards = [replace(card, not_now=True)]
    if rank is not None:
        cards.append(replace(card, day=DAY + timedelta(days=1), risk_rank=rank))
    result = score([client], cards, [DAY, DAY + timedelta(days=1)], feedback=True)
    assert result.gates["suppression"] == (rank != 2)
    assert set(result.gates) == {"budget", "suppression"}


@pytest.mark.parametrize("detected", [0, 1, 2, 3])
def test_timing_counts_missing_churn_and_uses_notice_date(detected: int) -> None:
    client, card = world()
    clients = [replace(client, key=str(i), notice_at=DAY + timedelta(days=30)) for i in range(3)]
    cards = [replace(card, client=str(i)) for i in range(detected)]
    result = score(clients, cards, [DAY])
    assert result.gates["timing"] == (detected >= 2)
    assert result.metrics["lead_days"]["0"] == (30 if detected else None)


@pytest.mark.parametrize("important", [False, True])
def test_benign_controls(important: bool) -> None:
    client, card = world()
    result = score(
        [replace(client, max_risk="watch")],
        [replace(card, importance="important" if important else "attention")],
        [DAY],
    )
    assert result.gates["benign"] != important


@pytest.mark.parametrize(
    ("policy", "bases", "evidence", "passed"),
    [
        ("metadata_only", ("metadata",), frozenset(), True),
        ("metadata_only", ("evidence",), frozenset(), False),
        ("metadata_only", ("metadata",), frozenset({UUID(int=1)}), False),
        ("excluded", ("metadata",), frozenset(), False),
    ],
)
def test_policy(
    policy: str, bases: tuple[str, ...], evidence: frozenset[UUID], passed: bool
) -> None:
    client, card = world()
    result = score(
        [replace(client, policy=policy)],
        [replace(card, fact_bases=bases, evidence_ids=evidence)],
        [DAY],
    )
    assert result.gates["policy"] == passed
    assert result.as_json()["gates"] == result.gates


def test_expansion_fall_is_not_a_change_and_notice_crossing_is() -> None:
    client = ClientSeries(
        "c", [row(DAY - timedelta(days=1), "watch", None, "hypothesis"), row(DAY, "watch", "watch")]
    )
    assert score([client], [], [DAY]).gates["exactness"]
    client = replace(client, renewal_date=DAY + timedelta(days=14), notice_period_days=0)
    card = CardObs(
        "c", DAY, "morning_scan", "state_change", "notice_deadline_close", "important", 1
    )
    assert score([client], [card], [DAY]).gates["exactness"]


@pytest.mark.parametrize("case", ["pass", "disabled", "missing_control", "empty"])
def test_suppression_probe_requires_positive_control_and_actual_suppression(case: str) -> None:
    _, card = world()
    result = score_suppression_probe(
        {} if case == "empty" else {"c": DAY},
        [] if case == "missing_control" else [card],
        [card] if case == "disabled" else [],
    )
    assert result.gates["suppression"] == (case == "pass")
    if case == "pass":
        assert result.metrics == {
            "probe_repeats_suppressed": 1,
            "probe_repeats_without_feedback": 1,
        }
