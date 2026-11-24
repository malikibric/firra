"""Evidence/replay contract shaping with the same synthetic fixtures as the TS tests."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from firra.local.routes.evidence_builders import shape_evidence, shape_replay, shape_run

NOW = datetime(2026, 10, 5, tzinfo=UTC)
CID = UUID(int=1)
RID = UUID(int=2)
EID = str(UUID(int=3))
NARRATIVE: dict[str, Any] = dict(
    status="active",
    end_date=None,
    end_kind=None,
    detection_at="2026-09-23",
    detection_state="high",
    confirmed_at="2026-09-30",
    lead_days=None,
    confirmed_lead_days=None,
    max_state="high",
    weeks_elevated=2,
    transitions=[],
    visible_at_detection=[
        dict(
            type="delivery_complaint",
            family="delivery",
            severity="high",
            basis="evidence",
            occurred_at="2026-09-23",
            primary=True,
            evidence_ids=[EID],
        )
    ],
    detection_timing=[],
    detection_play="fix_first",
    caveats=[],
    last_as_of="2026-09-30",
    last_state="high",
    unconfirmed_at_end=False,
    events=[],
)
TOTALS: dict[str, Any] = dict(
    losses=0,
    losses_detected_30d=0,
    median_lead_days=None,
    retained=1,
    retained_with_elevated=1,
    false_alarm_client_weeks_per_10_clients_per_month=2,
    flicker=0,
)


def test_evidence_selects_client_author_and_trimmed_subject() -> None:
    row: dict[str, Any] = dict(
        id=UUID(int=3),
        client_id=CID,
        quote="Synthetic verified evidence quote",
        locator=dict(start=0, end=33),
        deep_link="https://example.test/synthetic",
        source_record_id=UUID(int=4),
        occurred_at="2025-01-01T02:00:00+02:00",
        metadata=dict(subject="  " + "S" * 140 + "  ", private_extra="PRIVATE BODY"),
        side="client",
        display_name="Synthetic stakeholder",
        role_title="CEO",
        body_clean="PRIVATE BODY",
    )
    shaped = shape_evidence(row)
    assert len(shaped["subject"]) == 120 and shaped["author_name"] == "Synthetic stakeholder"
    assert shaped["author_role"] == "CEO" and len(shaped["source_ref"]) == 8
    assert shaped["occurred_at"].startswith("2025-01-01T00:00:00")
    assert "PRIVATE BODY" not in str(shaped)
    shaped = shape_evidence({**row, "side": "firm"})
    assert shaped["author_name"] is None and shaped["author_role"] is None
    with pytest.raises(ValueError):
        shape_evidence({**row, "quote": "short"})


def test_replay_preserves_narrative_and_collects_ids_once() -> None:
    body = shape_replay(NARRATIVE, [], policy="full", run_id=RID, client_id=CID, now=NOW)
    assert body is not None and body["narrative"] == NARRATIVE
    assert body["evidence_ids"] == [EID]
    assert (
        shape_replay(NARRATIVE, [], policy="metadata_only", run_id=RID, client_id=CID, now=NOW)
        is None
    )


def test_replay_metadata_and_unknown_fields_are_safe() -> None:
    narrative = deepcopy(NARRATIVE)
    narrative.update(visible_at_detection=[], private_extra="PRIVATE BODY")
    body = shape_replay(narrative, [], policy="metadata_only", run_id=RID, client_id=CID, now=NOW)
    assert body is not None and body["evidence_ids"] == [] and "PRIVATE BODY" not in str(body)
    narrative["visible_at_detection"] = [
        dict(
            type="renewal_window",
            family="commercial",
            severity="low",
            basis="metadata",
            occurred_at=None,
            primary=False,
            evidence_ids=[],
            inputs=dict(days_to_notice=2, quote="PRIVATE BODY"),
            private_extra="PRIVATE BODY",
        )
    ]
    body = shape_replay(narrative, [], policy="metadata_only", run_id=RID, client_id=CID, now=NOW)
    assert body is not None and "PRIVATE BODY" not in str(body)
    assert body["narrative"]["visible_at_detection"][0]["inputs"] == dict(days_to_notice=2)


def test_run_narrated_only_when_succeeded() -> None:
    row: dict[str, Any] = dict(
        id=RID,
        window_start="2026-09-01",
        window_end="2026-09-30",
        step_days=7,
        status="succeeded",
        finished_at=NOW,
        summary=dict(totals=TOTALS, private_extra="PRIVATE BODY"),
        narrated=True,
    )
    assert shape_run(row)["narrated"] is True and shape_run(row)["totals"] == TOTALS
    assert shape_run({**row, "status": "running"})["narrated"] is False
    assert shape_run({**row, "summary": None})["totals"] is None
    assert "PRIVATE BODY" not in str(shape_run(row))
