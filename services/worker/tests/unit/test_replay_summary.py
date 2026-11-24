"""Replay steps, the Churn Replay summary (D8) and a replayed Northbeam loss."""

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest

from firra.assessment.config import load_assessment_config, load_plays_config
from firra.assessment.guard import volume_rule_types
from firra.assessment.replay import replay_series, replay_steps
from firra.assessment.summary import ClientSeries, Step, detection, flicker, summarize
from firra.signals.config import SignalsConfig, load_config
from firra.signals.rules import RULES
from firra.synthetic.facts import client_facts
from firra.synthetic.plan import build_plan
from firra.synthetic.spec import load_spec

START = date(2026, 1, 5)


def _steps(*states: str, caveats: dict[int, tuple[str, ...]] | None = None) -> list[Step]:
    return [
        Step(START + timedelta(days=7 * i), s, (caveats or {}).get(i, ()))  # type: ignore[arg-type]
        for i, s in enumerate(states)
    ]


def test_replay_steps() -> None:
    assert replay_steps(date(2026, 1, 1), date(2026, 1, 20), 7) == [
        date(2026, 1, 1),
        date(2026, 1, 8),
        date(2026, 1, 15),
        date(2026, 1, 20),
    ]
    assert replay_steps(date(2026, 1, 1), date(2026, 1, 15), 7) == [
        date(2026, 1, 1),
        date(2026, 1, 8),
        date(2026, 1, 15),
    ]
    assert replay_steps(date(2026, 1, 1), date(2026, 3, 1), 7, stop=date(2026, 1, 10)) == [
        date(2026, 1, 1),
        date(2026, 1, 8),
        date(2026, 1, 10),
    ]
    assert replay_steps(date(2026, 1, 1), date(2026, 3, 1), 7, stop=date(2025, 12, 1)) == []


def test_detection_needs_two_consecutive_steps() -> None:
    assert detection(_steps("watch", "elevated", "watch", "high", "elevated")) == START + timedelta(
        days=21
    )
    assert detection(_steps("stable", "elevated")) is None


def test_flicker() -> None:
    assert flicker(_steps("watch", "elevated", "watch", "elevated")) == 1
    assert flicker(_steps("watch", "elevated", "elevated", "watch")) == 0
    assert flicker(_steps("stable", "watch", "stable", "watch", "stable", "watch")) == 2


def _series(states: list[str], **kwargs: object) -> ClientSeries:
    caveats = kwargs.pop("caveats", None)
    return ClientSeries(
        client_id=kwargs.pop("client_id", uuid4()),  # type: ignore[arg-type]
        status=kwargs.pop("status", "churned"),  # type: ignore[arg-type]
        steps=_steps(*states, caveats=caveats),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("states", "extra", "lead", "reason"),
    [
        (
            ["watch", "elevated", "high", "high"],
            {"churned_at": START + timedelta(days=80)},
            73,
            None,
        ),
        (
            ["watch", "elevated", "high", "high"],
            {"churned_at": START + timedelta(days=80), "notice_at": START + timedelta(days=40)},
            33,
            None,
        ),
        (
            ["watch", "watch", "elevated", "high"],
            {"churned_at": START + timedelta(days=40)},
            26,
            "late",
        ),
        (
            ["watch", "watch", "watch"],
            {"churned_at": START + timedelta(days=30)},
            None,
            "no_signal",
        ),
        (
            ["watch", "watch", "watch"],
            {"churned_at": START + timedelta(days=30), "caveats": {1: ("coverage_changed",)}},
            None,
            "coverage_changed",
        ),
        (
            ["stable", "stable"],
            {"churned_at": START + timedelta(days=30), "caveats": {1: ("low_data",)}},
            None,
            "low_data",
        ),
    ],
)
def test_losses(
    states: list[str], extra: dict[str, object], lead: int | None, reason: str | None
) -> None:
    summary = summarize(
        [_series(states, **extra)],
        window_start=START,
        window_end=START + timedelta(days=90),
        step_days=7,
    )
    client = summary.clients[0]
    assert (client.lead_days, client.miss_reason) == (lead, reason)


def test_totals() -> None:
    lost_early = _series(["elevated", "high", "high"], churned_at=START + timedelta(days=60))
    lost_late = _series(["watch", "elevated", "high"], churned_at=START + timedelta(days=20))
    calm = _series(["stable"] * 4, status="active")
    noisy = _series(["watch", "elevated", "elevated", "watch"], status="active")
    summary = summarize(
        [lost_early, lost_late, calm, noisy],
        window_start=START,
        window_end=START + timedelta(days=61),
        step_days=7,
    )
    totals = summary.totals
    assert totals["losses"] == 2 and totals["losses_detected_30d"] == 1
    assert totals["median_lead_days"] == (60 + 13) / 2
    assert totals["retained"] == 2 and totals["retained_with_elevated"] == 1
    # 2 elevated weeks over 2 retained clients (0.2 of ten) and ~2 months.
    assert totals["false_alarm_client_weeks_per_10_clients_per_month"] == pytest.approx(
        2 / 0.2 / (61 / (365.25 / 12)), rel=1e-3
    )
    stored = summary.as_json()
    assert set(stored) == {"clients", "totals"}
    assert all(isinstance(c["client_id"], str) for c in stored["clients"])


def test_a_northbeam_loss_is_detected_early_without_hindsight() -> None:
    spec = load_spec()
    plan = build_plan(spec)
    base = load_config()
    signals = SignalsConfig(
        version=base.version,
        rules={n: {**p, "enabled": n in RULES} for n, p in base.rules.items()},
    )
    client = next(c for c in spec.clients if c.key == "redfern")
    churned = spec.end_of(client)
    steps = replay_steps(churned - timedelta(days=180), churned, 7, stop=churned)
    series = replay_series(
        lambda ref: client_facts(spec, plan, client, ref),
        steps,
        signals=signals,
        assessment=load_assessment_config(),
        plays=load_plays_config(),
        volume_types=volume_rule_types(),
    )
    summary = summarize(
        [
            ClientSeries(
                client_id=UUID(int=1),
                status="churned",
                steps=[Step(a.as_of, a.risk_state) for a in series],
                churned_at=churned,
            )
        ],
        window_start=steps[0],
        window_end=churned,
        step_days=7,
    )
    loss = summary.clients[0]
    assert loss.miss_reason is None and loss.lead_days is not None and loss.lead_days >= 30
    assert loss.flicker == 0
