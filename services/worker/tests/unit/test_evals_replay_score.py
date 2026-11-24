"""Replay scoring: perfect-extraction series pass, and every metric fails on its own edge."""

import json
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from firra.assessment.replay import replay_steps
from firra.db.evals import _violation_class
from firra.evals.models import (
    AssessmentObs,
    CardObs,
    EvalInputs,
    ItemObs,
    NeedObs,
    Truth,
    TruthItem,
)
from firra.evals.replay_score import score_replay
from firra.evals.report import write_report
from firra.evals.run import REPLAY_DAYS, REPLAY_STEP_DAYS
from firra.evals.score import score
from firra.synthetic.assessment import weekly_assessments
from firra.synthetic.plan import build_plan
from firra.synthetic.spec import Client, Spec, load_spec
from firra.synthetic.truth import TRUTH_PATH
from tests.unit.test_evals_score import CORPUS as SCORE_CORPUS
from tests.unit.test_evals_score import PERFECT as SCORE_PERFECT
from tests.unit.test_evals_score import TRUTH as SCORE_TRUTH

AS_OF = date(2026, 9, 30)


def eval_steps(spec: Spec, client: Client) -> list[date]:
    """The steps the eval run replays: one window for everyone, a loss stops at its churn date."""
    return replay_steps(
        spec.as_of - timedelta(days=REPLAY_DAYS),
        spec.as_of,
        REPLAY_STEP_DAYS,
        stop=spec.end_of(client) if client.status == "churned" else None,
    )


def test_every_loss_has_replay_steps_before_its_deadline() -> None:
    spec = load_spec()
    losses = [c for c in spec.clients if c.status == "churned"]
    assert len(losses) == 3
    truth = json.loads(TRUTH_PATH.read_text())["clients"]
    for client in losses:
        steps = eval_steps(spec, client)
        deadline = date.fromisoformat(truth[client.key]["detect_by"])
        assert any(step <= deadline for step in steps), client.key
        assert steps[-1] == spec.end_of(client)


def truth_with(**clients: Mapping[str, Any]) -> Truth:
    base: dict[str, list[Any]] = {"signals": [], "latent_needs": [], "no_latent_needs": []}
    return Truth.model_validate(
        {
            "clients": {k: {"name": k.title(), **base, **v} for k, v in clients.items()},
            "items": [],
            "forbidden_quotes": [],
        }
    )


def series(
    name: str,
    risks: list[str],
    *,
    expansion: str = "none",
    play: str = "monitor",
    caveats: tuple[str, ...] = (),
    end: date = AS_OF,
) -> list[AssessmentObs]:
    return [
        AssessmentObs.model_validate(
            {
                "client": name.title(),
                "as_of": end - timedelta(days=7 * (len(risks) - 1 - i)),
                "risk": risk,
                "expansion": expansion,
                "play": play,
                "caveats": caveats,
            }
        )
        for i, risk in enumerate(risks)
    ]


ACTIVE = {
    "status": "active",
    "assessment": {"risk": "elevated", "expansion": "none", "play": "founder_review_call"},
}


def test_perfect_extraction_series_meet_every_dated_expectation() -> None:
    spec, truth = load_spec(), Truth.model_validate(json.loads(TRUTH_PATH.read_text()))
    plan = build_plan(spec)
    observations = [
        AssessmentObs(
            client=client.name,
            as_of=a.as_of,
            risk=a.risk_state,
            expansion=a.expansion_state,
            play=a.play,
            caveats=tuple(c.code for c in a.caveats),
        )
        for client in spec.clients
        for a in weekly_assessments(spec, plan, client, steps=eval_steps(spec, client))
    ]
    result = score_replay(observations, truth)
    assert result.failures == {}
    assert result.passed
    assert set(result.by_met) == {"umber", "vantage"} and all(result.by_met.values())
    assert (result.churned_detected, result.churned_checked) == (3, 3)
    assert result.risk_checked == result.play_checked == 12


def test_exact_state_at_the_last_week_is_required() -> None:
    truth = truth_with(a=ACTIVE, b=ACTIVE)
    obs = series("a", ["elevated"] * 4, play="founder_review_call") + series(
        "b", ["elevated"] * 3 + ["high"], play="founder_review_call"
    )
    result = score_replay(obs, truth)
    assert (result.risk_correct, result.risk_checked) == (1, 2)
    assert result.failures["risk_state"] == ["b"]
    assert not result.passed


def test_accuracy_threshold_is_eighty_percent_inclusive() -> None:
    clients = {f"c{i}": ACTIVE for i in range(5)}
    good = [
        o for i in range(4) for o in series(f"c{i}", ["elevated"] * 3, play="founder_review_call")
    ]
    bad = series("c4", ["watch"] * 3, play="founder_review_call")
    result = score_replay(good + bad, truth_with(**clients))
    assert (result.risk_correct, result.risk_checked) == (4, 5)
    assert result.failures == {"risk_state": ["c4"]}
    assert result.passed  # 0.8 is enough


def test_missing_observations_fail_the_run() -> None:
    result = score_replay([], truth_with(a=ACTIVE))
    assert result.failures["no_observations"] == ["a"]
    assert not result.passed


def test_by_date_needs_the_state_reached_in_time() -> None:
    expectation = {"risk": "elevated", "expansion": "none", "play": "monitor", "by": "2026-09-16"}
    truth = truth_with(a={"status": "active", "assessment": expectation})
    late = series("a", ["stable", "watch", "elevated", "elevated"], play="monitor")
    on_time = series("a", ["stable", "elevated", "elevated", "elevated"], play="monitor")
    assert score_replay(late, truth).by_met == {"a": False}
    assert score_replay(late, truth).failures["by_not_met"] == ["a"]
    assert score_replay(on_time, truth).by_met == {"a": True}


def test_by_date_covers_expansion_as_well_as_risk() -> None:
    expectation = {"risk": "watch", "expansion": "strong", "play": "monitor", "by": "2026-09-16"}
    truth = truth_with(a={"status": "active", "assessment": expectation})
    watch = series("a", ["watch"] * 4, expansion="strong", play="monitor")
    hypothesis_only = [o.model_copy(update={"expansion": "hypothesis"}) for o in watch]
    assert score_replay(watch, truth).by_met == {"a": True}
    assert score_replay(hypothesis_only, truth).by_met == {"a": False}


def test_benign_control_must_never_exceed_its_ceiling() -> None:
    expectation = {"risk": "stable", "expansion": "none", "play": "monitor"}
    truth = truth_with(a={"status": "active", "assessment": expectation, "max_risk": "watch"})
    spike = series("a", ["stable", "elevated", "watch", "stable"])
    result = score_replay(spike, truth)
    assert result.max_risk_exceeded == ["a"] and not result.passed
    assert score_replay(series("a", ["stable", "watch", "watch", "stable"]), truth).passed


def test_churned_clients_are_detected_by_two_steps_at_elevated_before_the_date() -> None:
    gone = {"status": "churned", "detect_by": "2026-09-16"}
    truth = truth_with(x=gone, y=gone, z=gone)
    early = ["stable", "elevated", "elevated", "high"]
    late = ["stable", "stable", "elevated", "elevated"]
    lone_spike = ["stable", "elevated", "stable", "stable"]
    obs = series("x", early) + series("y", late) + series("z", lone_spike)
    result = score_replay(obs, truth)
    assert (result.churned_detected, result.churned_checked) == (1, 3)
    assert result.failures["churn_not_detected_in_time"] == ["y", "z"]
    assert not result.passed
    two_of_three = series("x", early) + series("y", early) + series("z", lone_spike)
    assert score_replay(two_of_three, truth).passed


def test_false_alarms_count_weeks_at_elevated_on_quiet_clients_per_month() -> None:
    quiet = {
        "status": "active",
        "assessment": {"risk": "stable", "expansion": "none", "play": "monitor"},
    }
    truth = truth_with(**{f"q{i}": quiet for i in range(10)})
    calm = [o for i in range(10) for o in series(f"q{i}", ["stable"] * 5)]
    assert score_replay(calm, truth).false_alarm_weeks_per_10_per_month == 0
    one_week = calm[:-1] + series("q9", ["stable"] * 4 + ["elevated"])[-1:]
    result = score_replay(one_week, truth)
    # 1 client-week over 10 clients in a 28-day window (0.92 months)
    assert result.false_alarm_weeks_per_10_per_month == pytest.approx(
        1 / (28 / (365.25 / 12)), abs=0.001
    )
    assert not result.passed


def test_elevated_weeks_on_clients_expected_elevated_are_not_false_alarms() -> None:
    truth = truth_with(a=ACTIVE)
    result = score_replay(series("a", ["elevated"] * 5, play="founder_review_call"), truth)
    assert result.false_alarm_weeks_per_10_per_month == 0 and result.passed


def test_flicker_is_up_down_up_within_four_weeks() -> None:
    truth = truth_with(a=ACTIVE)
    result = score_replay(
        series("a", ["watch", "elevated", "watch", "elevated"], play="founder_review_call"), truth
    )
    assert result.flicker == 1 and result.failures["flicker"] == ["a"] and not result.passed


def test_missing_caveats_are_reported_but_do_not_fail_the_run() -> None:
    truth = truth_with(a={**ACTIVE, "caveats": ["holiday_period"]})
    result = score_replay(series("a", ["elevated"] * 3, play="founder_review_call"), truth)
    assert result.caveats_missing == {"a": ["holiday_period"]} and result.passed


def test_unknown_clients_and_clients_without_expectations_are_ignored() -> None:
    truth = truth_with(a={"status": "active"})
    stray = series("stranger", ["high"] * 3)
    result = score_replay(stray + series("a", ["high"] * 3), truth)
    assert result.risk_checked == 0 and result.passed


def test_a_loss_without_a_detection_deadline_fails_instead_of_shrinking_the_gate() -> None:
    truth = truth_with(x={"status": "churned"}, y={"status": "churned", "detect_by": "2026-09-16"})
    result = score_replay(series("y", ["stable", "elevated", "elevated", "high"]), truth)
    assert (result.churned_detected, result.churned_checked) == (1, 1)
    assert result.failures == {"churn_without_detect_by": ["x"]}
    assert not result.passed
    assert not score_replay([], truth_with(x={"status": "churned"})).passed


def test_lead_days_are_reported_for_known_clients_with_their_median() -> None:
    truth = truth_with(x={"status": "churned", "detect_by": "2026-09-16"}, y={"status": "churned"})
    result = score_replay([], truth, lead_days={"x": 45, "y": None, "stranger": 9})
    assert result.lead_days == {"x": 45, "y": None}
    assert result.median_lead_days == 45
    assert score_replay([], truth).median_lead_days is None


def test_report_verdict_is_the_same_in_json_markdown_and_both_halves(tmp_path: Path) -> None:
    truth = truth_with(a=ACTIVE)
    base = score(EvalInputs(), truth_with(a=ACTIVE), {}).model_copy(update={"passed": True})
    failing = score_replay([], truth)
    write_report(base, tmp_path, failing)
    payload = json.loads((tmp_path / "eval.json").read_text())
    assert (payload["passed"], payload["base_passed"], payload["replay"]["passed"]) == (
        False,
        True,
        False,
    )
    assert "Overall: **FAIL**" in (tmp_path / "eval.md").read_text()
    passing = score_replay(series("a", ["elevated"] * 3, play="founder_review_call"), truth)
    write_report(
        base,
        tmp_path,
        passing,
    )
    assert json.loads((tmp_path / "eval.json").read_text())["passed"] is True
    assert "Overall: **PASS**" in (tmp_path / "eval.md").read_text()
    leads = score_replay(
        [], truth_with(x={"status": "churned", "detect_by": "2026-09-16"}), lead_days={"x": 45}
    )
    write_report(base, tmp_path, leads)
    assert "Lead days before notice or churn: x | 45" in (tmp_path / "eval.md").read_text()


@pytest.mark.parametrize(
    ("kind", "field", "expected", "observed", "matches"),
    [
        ("renewal_or_budget_talk", "budget_direction", "cut", "cut", True),
        ("renewal_or_budget_talk", "budget_direction", "cut", "grow", False),
        ("renewal_or_budget_talk", "budget_direction", "cut", None, False),
        ("initiative", "initiative_type", "market", "market", True),
        ("initiative", "initiative_type", "market", "hire", False),
    ],
)
def test_v2_item_fields_must_match_when_truth_states_them(
    kind: str, field: str, expected: str, observed: str | None, matches: bool
) -> None:
    quote = "Our marketing budget for the second half is being cut by 40%."
    truth = Truth(
        clients={},
        items=[TruthItem(external_id="m1", kind=kind, quote=quote, **{field: expected})],
        forbidden_quotes=[],
    )
    item = ItemObs(external_id="m1", kind=kind, quote=quote, **{field: observed})
    result = score(EvalInputs(items=[item]), truth, {})
    assert result.extraction.recall == (1.0 if matches else 0.0)


def _card_truth() -> Truth:
    return truth_with(
        a={
            "status": "active",
            "signals": ["delivery_complaint"],
            "assessment": {"risk": "high", "expansion": "none", "play": "fix_first"},
        },
        b={
            "status": "active",
            "assessment": {"risk": "stable", "expansion": "none", "play": "monitor"},
        },
    )


def test_a_card_stage_that_wrote_nothing_does_not_pass() -> None:
    result = score(EvalInputs(), _card_truth(), {})
    assert (result.cards_expected, result.cards_checked) == (1, 0)
    card = CardObs(client="a", kind="risk", source_signal_types=["delivery_complaint"])
    with_card = score(EvalInputs(cards=[card]), _card_truth(), {})
    assert (with_card.cards_expected, with_card.cards_checked) == (1, 1)
    assert with_card.false_positive_cards == 0


def test_a_stored_card_that_fails_its_integrity_check_is_reported_by_class_only() -> None:
    bad = CardObs(
        client="a",
        kind="risk",
        source_signal_types=["delivery_complaint"],
        integrity_violations=["number:title"],
    )
    result = score(EvalInputs(cards=[bad]), _card_truth(), {})
    assert result.card_integrity_failures == 1
    assert result.failures["card_integrity"] == ["a:risk:0:number:title"]
    assert not result.passed


def _play_truth(*, quillon_signals: tuple[str, ...] = ()) -> Truth:
    """TRUTH with alpha expecting a play (so a card is required) and quillon monitor-only."""
    clients = {k: v.model_dump(mode="json") for k, v in SCORE_TRUTH.clients.items()}
    clients["alpha"]["assessment"] = {"risk": "high", "expansion": "none", "play": "fix_first"}
    clients["quillon"]["assessment"] = {"risk": "stable", "expansion": "none", "play": "monitor"}
    clients["quillon"]["signals"] = list(quillon_signals)
    return Truth.model_validate({**SCORE_TRUTH.model_dump(mode="json"), "clients": clients})


def test_card_gates_each_fail_on_their_own_with_everything_else_passing() -> None:
    truth = _play_truth()
    assert score(SCORE_PERFECT, truth, SCORE_CORPUS).passed
    # Only the card stage differs from the passing run in each case below.
    no_cards = score(SCORE_PERFECT.model_copy(update={"cards": []}), truth, SCORE_CORPUS)
    assert not no_cards.passed and no_cards.failures == {}
    broken = SCORE_PERFECT.cards[0].model_copy(update={"integrity_violations": ["number:title"]})
    integrity = score(SCORE_PERFECT.model_copy(update={"cards": [broken]}), truth, SCORE_CORPUS)
    assert not integrity.passed and set(integrity.failures) == {"card_integrity"}


def test_a_card_on_a_monitor_only_account_is_a_false_positive_even_with_a_matching_driver() -> None:
    truth = _play_truth(quillon_signals=("tone_decline",))
    card = CardObs(client="quillon", kind="risk", source_signal_types=["tone_decline"])
    result = score(
        SCORE_PERFECT.model_copy(update={"cards": [*SCORE_PERFECT.cards, card]}),
        truth,
        SCORE_CORPUS,
    )
    assert result.failures["false_positive_cards"] == ["quillon:risk:1"]
    assert not result.passed


def test_a_card_for_a_client_truth_does_not_know_is_a_false_positive() -> None:
    card = CardObs(client="unknown_client", kind="risk", source_signal_types=["tone_decline"])
    result = score(
        SCORE_PERFECT.model_copy(update={"cards": [*SCORE_PERFECT.cards, card]}),
        _play_truth(),
        SCORE_CORPUS,
    )
    assert result.failures["false_positive_cards"] == ["unknown_client:risk:1"]


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_the_exact_need_wins_whatever_order_the_clusters_arrive_in(order: tuple[int, int]) -> None:
    exact = NeedObs(client="alpha", topic="reporting", service="analytics", mentions=3, threads=2)
    extra = NeedObs(client="alpha", topic="dashboards", service="analytics", mentions=5, threads=4)
    needs = [(exact, extra)[i] for i in order]
    result = score(SCORE_PERFECT.model_copy(update={"needs": needs}), SCORE_TRUTH, SCORE_CORPUS)
    assert (result.latent_needs_correct, result.latent_needs_expected) == (1, 1)
    assert "incorrect_needs" not in result.failures


@pytest.mark.parametrize(
    ("problem", "kept"),
    [
        ("unknown_fact:Secret phrase in a fact id", "unknown_fact"),
        ("number:title:45", "number:title"),
        ("number_word:interpretation:seven", "number_word:interpretation"),
        ("quotation:recommendation", "quotation:recommendation"),
        ("empty:title", "empty:title"),
        ("no_fact_ids", "no_fact_ids"),
        ("number:Secret phrase:45", "number"),
    ],
)
def test_violation_artifacts_keep_a_class_and_a_known_field_only(problem: str, kept: str) -> None:
    assert _violation_class(problem) == kept


def test_additional_need_clusters_are_listed_without_changing_the_verdict() -> None:
    exact = NeedObs(client="alpha", topic="reporting", service="analytics", mentions=3, threads=2)
    extra = NeedObs(
        client="alpha", topic="secret topic", service="analytics", mentions=4, threads=3
    )
    alpha = SCORE_TRUTH.clients["alpha"].model_copy(update={"no_latent_needs": []})
    truth = SCORE_TRUTH.model_copy(update={"clients": {**SCORE_TRUTH.clients, "alpha": alpha}})
    result = score(SCORE_PERFECT.model_copy(update={"needs": [exact, extra]}), truth, SCORE_CORPUS)
    assert result.passed and result.latent_needs_correct == 1
    assert result.failures["extra_needs"] == ["alpha:analytics:4/3"]
    assert "secret topic" not in str(result.failures)
