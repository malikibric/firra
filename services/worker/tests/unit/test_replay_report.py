"""Churn Replay narrative and report: deterministic text from stored drivers plus quotes."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from firra.assessment.narrative import StepRow, build_narrative
from firra.assessment.report import QuoteView, render_client, render_index

START = date(2026, 1, 5)
EVIDENCE = UUID("00000000-0000-0000-0000-00000000000e")
END = START + timedelta(days=84)


def _driver(type_: str, family: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": type_,
        "family": family,
        "severity": "medium",
        "basis": "metadata",
        "dedupe_key": type_,
        "occurred_at": f"{START.isoformat()}T09:00:00+00:00",
        "inputs": None,
        "evidence_ids": [],
        "primary": True,
        **extra,
    }


COMPLAINT = _driver(
    "delivery_complaint",
    "delivery",
    basis="evidence",
    evidence_ids=[str(EVIDENCE)],
    occurred_at="2026-02-09T09:00:00+00:00",
)
SILENCE = _driver(
    "decision_maker_silence",
    "stakeholder",
    inputs={"last_message_at": "2026-01-20T10:00:00+00:00", "messages_31_180": 7},
)
SCOPE = _driver("scope_cut_talk", "commercial")


def _row(week: int, risk: str, *drivers: dict[str, Any], play: str = "monitor") -> StepRow:
    return StepRow(
        as_of=START + timedelta(days=7 * week),
        risk=risk,  # type: ignore[arg-type]
        expansion="none",
        play=play,
        strength="moderate",
        drivers=drivers,
        caveats=(),
    )


ROWS = [
    _row(0, "stable"),
    _row(1, "watch", SILENCE),
    _row(2, "watch", SILENCE),
    _row(5, "elevated", SILENCE, COMPLAINT, play="fix_first"),
    _row(6, "elevated", SILENCE, COMPLAINT, SCOPE, play="fix_first"),
    _row(8, "high", SILENCE, COMPLAINT, SCOPE, play="fix_first"),
]


def _narrative() -> Any:
    return build_narrative(
        ROWS,
        client_name="Redfern",
        status="churned",
        churned_at=END + timedelta(days=20),
        notice_at=END,
    )


def test_transitions_new_drivers_and_detection() -> None:
    n = _narrative()
    assert [(t.as_of, t.from_state, t.to_state) for t in n.transitions] == [
        (START + timedelta(days=7), "stable", "watch"),
        (START + timedelta(days=35), "watch", "elevated"),
        (START + timedelta(days=56), "elevated", "high"),
    ]
    assert [d.type for d in n.transitions[0].new_drivers] == ["decision_maker_silence"]
    assert [d.type for d in n.transitions[1].new_drivers] == ["delivery_complaint"]
    assert n.transitions[2].new_drivers == ()
    assert (n.detection_at, n.detection_state) == (START + timedelta(days=35), "elevated")
    assert n.lead_days == 49 and n.end_kind == "notice" and n.max_state == "high"
    assert n.transitions[1].days_before_end == 49
    assert [d.type for d in n.visible_at_detection] == [
        "decision_maker_silence",
        "delivery_complaint",
    ]
    assert n.detection_play == "fix_first"


def test_report_shows_dated_drivers_quotes_and_no_hindsight_note() -> None:
    quotes = {
        EVIDENCE: QuoteView(
            "We are still waiting for the Q3 report", date(2026, 2, 9), "https://mail/x"
        )
    }
    text = render_client(_narrative(), quotes, window=(START, END))
    assert text.startswith("# Redfern — Churn Replay\n")
    assert "no hindsight" in text
    assert "**49 days before the notice on 2026-03-30**" in text
    assert "First alert: Redfern moved to **elevated** in the week of 2026-02-09" in text
    assert "The following week (2026-02-16) confirmed it, 42 days before the notice" in text
    assert "- The client complained about delivery — 2026-02-09" in text
    assert (
        '  - "We are still waiting for the Q3 report" — 2026-02-09 · [open](https://mail/x)' in text
    )
    assert (
        "No recent message from a client decision maker (last message: 2026-01-20; "
        "earlier messages: 7)" in text
    )
    assert "### 2026-01-12: stable → watch" in text
    assert "### 2026-02-09: watch → elevated, 49 days before the notice" in text
    assert "Suggested next step: Check the open complaint or late commitment" in text
    assert "`fix_first`" not in text
    assert "## How to read this" in text
    assert "score of any person" in text


def test_missing_quote_is_skipped_and_metadata_only_is_stated() -> None:
    text = render_client(_narrative(), {}, window=(START, END), metadata_only=True)
    assert '  - "' not in text
    assert text.index("Metadata only") < text.index("First alert")


def test_a_loss_without_detection_and_a_calm_client() -> None:
    quiet = build_narrative(
        [_row(0, "stable"), _row(1, "watch", SILENCE), _row(2, "stable")],
        client_name="Ostara",
        status="churned",
        churned_at=END,
    )
    assert quiet.detection_at is None and quiet.lead_days is None
    text = render_client(quiet, {}, window=(START, END))
    assert "would not have raised an alert for Ostara before the churn on 2026-03-30" in text
    calm = build_narrative(
        [_row(0, "stable"), _row(1, "stable")], client_name="Quillon", status="active"
    )
    assert calm.transitions == ()
    assert "The state stayed stable for the whole window." in render_client(
        calm, {}, window=(START, END)
    )
    assert "never stayed at elevated or high for two weeks" in render_client(
        calm, {}, window=(START, END)
    )


def test_index_lists_losses_and_retained_false_alarms() -> None:
    noisy = build_narrative(
        [
            _row(0, "watch", SILENCE),
            _row(1, "elevated", SILENCE, COMPLAINT),
            _row(2, "elevated", SILENCE, COMPLAINT),
        ],
        client_name="Pinecrest",
        status="active",
    )
    text = render_index(
        [_narrative(), noisy],
        filenames=["redfern.md", "pinecrest.md"],
        window=(START, END),
        totals={
            "losses": 1,
            "losses_detected_30d": 1,
            "median_lead_days": 49,
            "false_alarm_client_weeks_per_10_clients_per_month": 3.5,
        },
    )
    assert "| [Redfern](redfern.md) | 2026-02-09 | 2026-02-16 | 49 | high | — |" in text
    assert "| [Pinecrest](pinecrest.md) | elevated | 2 | elevated (unconfirmed) |" not in text
    assert "| [Pinecrest](pinecrest.md) | elevated | 2 | elevated |" in text
    assert "Losses detected 30+ days ahead: 1 of 1. Median lead: 49 days." in text
    assert "(2 elevated or high assessments, 1 retained clients, 2.8 months)" in text


def test_a_repeated_quote_is_printed_once() -> None:
    quotes = {EVIDENCE: QuoteView("We are still waiting", date(2026, 2, 9), None)}
    again = _driver(
        "repeated_complaint", "delivery", basis="evidence", evidence_ids=[str(EVIDENCE)]
    )
    narrative = build_narrative(
        [_row(0, "stable"), _row(1, "watch", COMPLAINT), _row(2, "elevated", COMPLAINT, again)],
        client_name="Redfern",
        status="active",
    )
    text = render_client(narrative, quotes, window=(START, END))
    assert text.count('"We are still waiting"') == 1
    assert "Same message as quoted above." in text


def test_a_final_unconfirmed_alert_and_the_initiation_baseline_unit() -> None:
    initiation = _driver(
        "client_initiation_drop",
        "engagement",
        inputs={"initiated_30": 1, "baseline_per_30": 2.0},
    )
    narrative = build_narrative(
        [_row(0, "stable"), _row(1, "high", initiation)],
        client_name="Meridian",
        status="active",
    )
    assert narrative.unconfirmed_at_end and narrative.confirmed_at is None
    text = render_client(narrative, {}, window=(START, END), metadata_only=True)
    assert "no following week confirms it" in text
    assert "usual conversations started by the client per 30 days: 2" in text
    assert "usual messages per 30 days" not in text
