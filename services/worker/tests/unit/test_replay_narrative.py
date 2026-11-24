"""Chronology regression tests from the T26c brief."""

from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from firra.assessment.narrative import build_narrative
from firra.assessment.report import QuoteView, render_client
from tests.unit.test_replay_report import COMPLAINT, END, EVIDENCE, START, _driver, _row

SECOND = UUID("00000000-0000-0000-0000-00000000000f")


def test_updates_without_state_change_and_chronology() -> None:
    updated = {**COMPLAINT, "evidence_ids": [str(EVIDENCE), str(SECOND)], "severity": "high"}
    departure = _driver("champion_departure", "stakeholder")
    rows = [_row(0, "watch", COMPLAINT), _row(1, "watch", updated, departure), _row(2, "stable")]
    n = build_narrative(list(reversed(rows)), client_name="Acme", status="active")
    assert [e.kind for e in n.events] == [
        "new_evidence",
        "severity_changed",
        "new_driver",
        "ended",
        "ended",
    ]
    assert n.events[0].driver is not None
    assert n.events[0].driver.evidence_ids == (SECOND,)
    assert n.events[1].old_severity == "medium"
    assert n.events[1].driver is not None and n.events[1].driver.severity == "high"
    assert [e.as_of for e in n.events] == sorted(e.as_of for e in n.events)
    text = render_client(n, {}, window=(START, END))
    assert "## Timeline" in text and "## State changes" not in text
    assert "Severity changed: The client complained about delivery: medium → high" in text
    assert "No longer counted from 2026-01-19" in text
    assert "Rolling windows let signals age out; this does not show the issue was resolved." in text
    assert "The state drop reflects only drivers no longer counted" in text
    assert (
        text.index("### 2026-01-05") < text.index("### 2026-01-12") < text.index("### 2026-01-19")
    )


def test_all_active_causes_primary_first_and_timing_at_detection() -> None:
    timing = _driver(
        "renewal_window", "timing", primary=False, inputs={"notice_deadline": "2026-03-30"}
    )
    n = build_narrative(
        [
            _row(0, "watch", COMPLAINT),
            _row(1, "elevated", timing, COMPLAINT),
            _row(2, "elevated", timing, COMPLAINT),
        ],
        client_name="Acme",
        status="active",
    )
    t = n.transitions[1]
    assert [d.type for d in t.active_drivers] == ["delivery_complaint", "renewal_window"]
    assert [d.type for d in t.new_drivers] == ["renewal_window"]
    assert [d.type for d in n.visible_at_detection] == ["delivery_complaint"]
    assert [d.type for d in n.detection_timing] == ["renewal_window"]
    text = render_client(n, {}, window=(START, END))
    visible, timeline = text.split("## Timeline")
    assert "notice deadline: 2026-03-30" in visible
    assert "The contract renewal is approaching (notice deadline: 2026-03-30) [new]" in timeline
    assert "The client complained about delivery" in timeline.split("### 2026-01-12")[1]


def test_known_outcomes_and_post_loss_drop() -> None:
    known, notice, churn = date(2026, 1, 6), date(2026, 1, 9), date(2026, 1, 12)
    n = build_narrative(
        [_row(0, "high", COMPLAINT), _row(1, "watch")],
        client_name="Acme",
        status="churned",
        churned_at=churn,
        notice_at=notice,
        first_known_at=known,
    )
    text = render_client(n, {}, window=(START, END))
    assert "The firm learned of the loss: 2026-01-06" in text
    assert "Recorded notice date: 2026-01-09" in text
    assert "Recorded churn date: 2026-01-12" in text
    assert (
        text.index("### 2026-01-06") < text.index("### 2026-01-09") < text.index("### 2026-01-12")
    )
    assert "this state does not establish recovery" in text


def test_identical_evidence_grouped_distinct_messages_separate() -> None:
    second_rule = _driver(
        "executive_escalation", "delivery", evidence_ids=[str(EVIDENCE)], basis="evidence"
    )
    distinct = _driver("scope_cut_talk", "commercial", evidence_ids=[str(SECOND)], basis="evidence")
    n = build_narrative(
        [_row(0, "high", COMPLAINT, second_rule, distinct)], client_name="Acme", status="active"
    )
    quotes = {
        EVIDENCE: QuoteView("Synthetic complaint", START, None),
        SECOND: QuoteView("Synthetic cut", START, None),
    }
    text = render_client(n, quotes, window=(START, END))
    assert (
        "The client complained about delivery [new]; "
        "A senior client stakeholder raised a complaint [new]" in text
    )
    assert "One message, read by 2 rules." in text
    assert text.count('"Synthetic complaint"') == text.count('"Synthetic cut"') == 1
    assert "- The client talked about cutting budget or scope [new]" in text


def test_evidence_context_and_metadata_boundary() -> None:
    n = build_narrative([_row(0, "watch", COMPLAINT)], client_name="Acme", status="active")
    quote = QuoteView(
        "Synthetic complaint",
        START,
        "https://example.test/mail",
        "Ana",
        "CMO",
        "Report",
        "ab12cd34",
    )
    text = render_client(n, {EVIDENCE: quote}, window=(START, END))
    assert (
        '"Synthetic complaint" — Ana, CMO · "Report" · 2026-01-05 · source ab12cd34 · [open](https://example.test/mail)'
        in text
    )
    firm = replace(quote, author_name="our team", author_role=None, subject=None, deep_link=None)
    text = render_client(n, {EVIDENCE: firm}, window=(START, END))
    assert (
        '"Synthetic complaint" — our team · 2026-01-05 · source ab12cd34 · source link unavailable'
        in text
    )
    assert "CMO" not in text and '"Report"' not in text
    assert '"Synthetic complaint"' not in render_client(
        n, {EVIDENCE: quote}, window=(START, END), metadata_only=True
    )
    missing = replace(
        quote,
        author_name=None,
        author_role=None,
        subject=None,
        occurred_at=None,
        source_ref=None,
        deep_link=None,
    )
    assert '"Synthetic complaint" · source link unavailable' in render_client(
        n, {EVIDENCE: missing}, window=(START, END)
    )


def test_irrelevant_driver_events_are_excluded() -> None:
    growth = _driver("expansion_need", "growth")
    unknown = _driver("unknown", "delivery")
    n = build_narrative(
        [_row(0, "watch", growth, unknown), _row(1, "stable")], client_name="Acme", status="active"
    )
    assert n.events == ()
    assert all(not t.active_drivers for t in n.transitions)


def test_updated_and_new_rules_sharing_a_message_group_together() -> None:
    updated = {**COMPLAINT, "evidence_ids": [str(EVIDENCE), str(SECOND)]}
    repeated = {**updated, "type": "repeated_complaint", "dedupe_key": "repeated"}
    n = build_narrative(
        [_row(0, "watch", COMPLAINT), _row(1, "watch", updated, repeated)],
        client_name="Acme",
        status="active",
    )
    text = render_client(
        n,
        {
            EVIDENCE: QuoteView("First synthetic message", START, None),
            SECOND: QuoteView("Second synthetic message", START, None),
        },
        window=(START, END),
    )
    assert "- The client complained about delivery; The client complained repeatedly" in text
    assert text.count('"Second synthetic message"') == 1


def test_storage_round_trip_keeps_markdown_identical() -> None:
    import json

    from firra.assessment.narrative import narrative_from_json, narrative_to_json
    from tests.unit.test_replay_report import ROWS, SILENCE

    cases = [
        ROWS,
        [],
        [_row(0, "high", COMPLAINT)],
        [
            _row(0, "watch", SILENCE),
            _row(1, "high", COMPLAINT),
            _row(2, "high", COMPLAINT),
            _row(3, "watch", {**COMPLAINT, "evidence_ids": [str(SECOND)]}),
        ],
    ]
    for rows in cases:
        original = build_narrative(
            rows,
            client_name="Acme",
            status="churned",
            churned_at=END,
            notice_at=END,
            first_known_at=START,
        )
        stored = narrative_to_json(original)
        decoded = narrative_from_json(json.loads(json.dumps(stored)), client_name="Acme")
        assert narrative_to_json(decoded) == stored
        assert render_client(decoded, {}, window=(START, END)) == render_client(
            original, {}, window=(START, END)
        )
        text = json.dumps(stored)
        assert "Acme" not in text and '"label"' not in text and '"detail"' not in text
        assert "client messages" not in text


def test_storage_drops_untyped_metadata_and_evidence_inputs() -> None:
    from firra.assessment.narrative import narrative_to_json

    driver = {**COMPLAINT, "inputs": {"text": "PRIVATE BODY"}}
    stored = narrative_to_json(
        build_narrative([_row(0, "high", driver)], client_name="", status="active")
    )
    assert "PRIVATE BODY" not in str(stored)
    driver = _driver("reduced_engagement", "engagement", inputs={"text": "PRIVATE BODY"})
    stored = narrative_to_json(
        build_narrative([_row(0, "high", driver)], client_name="", status="active")
    )
    assert "PRIVATE BODY" not in str(stored)


def test_narrate_cli_refuses_remote_or_missing_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from firra.assessment.__main__ import main

    args = ["narrate", "--workspace", str(SECOND), "--run", str(EVIDENCE)]
    monkeypatch.delenv("FIRRA_DATABASE_URL", raising=False)
    assert main(args) == 2
    monkeypatch.setenv("FIRRA_DATABASE_URL", "postgresql://remote.invalid/test")
    assert main(args) == 2


def test_unknown_or_mistyped_metadata_inputs_are_dropped_not_fatal() -> None:
    from firra.assessment.narrative import metadata_inputs

    assert metadata_inputs(
        {
            "messages_30": 3,
            "baseline_per_30": "oops",
            "note": "free text",
            "renewal_date": "2026-03-31",
        }
    ) == {"messages_30": 3, "renewal_date": "2026-03-31"}
