"""Python twins of the TS read-model builder tests (synthetic records only)."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from firra.local.routes.client_builders import (
    build_attention,
    build_changes,
    build_client_detail,
    build_client_list_item,
    build_state_history,
)
from firra.local.routes.meeting_builder import build_meetings
from firra_contracts.generated_v1.attention_response import AttentionResponse
from firra_contracts.generated_v1.changes_response import ChangesResponse
from firra_contracts.generated_v1.meetings_response import MeetingsResponse

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
CID = str(UUID(int=2))
EID = str(UUID(int=3))
CLIENT: dict[str, Any] = dict(
    id=CID,
    name="Synthetic Portfolio Client",
    status="active",
    policy="full",
    mrr=4000,
    currency="EUR",
    renewal_date="2026-11-01",
    notice_period_days=30,
)
ASSESSMENT: dict[str, Any] = dict(
    client_id=CID,
    as_of="2026-10-01",
    risk_state="high",
    previous_risk_state="watch",
    state_since="2026-10-01",
    expansion_state="none",
    strength="moderate",
    play="fix_first",
    caveats=[
        dict(code="participant_coverage", detail="Few participants.", private_extra="PRIVATE BODY")
    ],
    drivers=[
        dict(
            type="delivery_complaint",
            family="delivery",
            severity="high",
            basis="evidence",
            occurred_at="2026-09-30T08:00:00Z",
            primary=True,
            evidence_ids=[EID],
            inputs=dict(quote="PRIVATE BODY"),
        ),
        dict(
            type="renewal_window",
            family="commercial",
            severity="medium",
            basis="metadata",
            occurred_at="2026-10-01T00:00:00Z",
            primary=False,
            evidence_ids=[EID],
            inputs=dict(days_to_notice=1, renewal_date="2026-11-01", quote="PRIVATE BODY"),
        ),
    ],
)
PARTS: dict[str, Any] = dict(
    cardContent=dict(
        unknowns=[dict(code="budget_unknown", detail="Budget not stated.")],
        private_extra="PRIVATE BODY",
    ),
    stakeholders=[
        dict(
            display_name="Synthetic Person",
            role_title=None,
            is_decision_maker=False,
            is_champion=True,
            last_seen_at="2026-09-30T08:00:00Z",
        )
    ],
    commitments=[
        dict(
            owner_side="firm",
            text="Synthetic commitment",
            due_date="2026-10-05",
            status="open",
            commitment_evidence=[dict(evidence_id=EID, created_at="2026-09-30T08:00:00Z")],
        )
    ],
    goals=[
        dict(
            text="Synthetic goal",
            metric="10",
            stated_at="2026-09-30T08:00:00Z",
            client_goal_evidence=[dict(evidence_id=EID, created_at="2026-09-30T08:00:00Z")],
        )
    ],
    needs=[
        dict(
            topic="Synthetic need",
            mention_count=3,
            thread_count=2,
            first_seen_at="2026-09-01T08:00:00Z",
            last_seen_at="2026-09-30T08:00:00Z",
            latent_need_evidence=[dict(evidence_id=EID, created_at="2026-09-30T08:00:00Z")],
        )
    ],
    outcomes=[
        dict(
            event="renewed",
            occurred_at="2026-09-30",
            notice_at=None,
            first_known_at=None,
            amount_change=None,
            currency=None,
        )
    ],
)


def test_attention_latest_ranking_and_safe_drivers() -> None:
    items = build_attention(
        [ASSESSMENT, {**ASSESSMENT, "as_of": "2026-09-01", "risk_state": "stable"}], [CLIENT], NOW
    )
    assert len(items) == 1 and items[0]["risk_state"] == "high"
    assert items[0]["days_to_notice"] == 1
    assert [d["type"] for d in items[0]["drivers"]] == ["delivery_complaint", "renewal_window"]
    assert items[0]["drivers"][0]["occurred_at"] == "2026-09-30"
    assert "PRIVATE BODY" not in str(items)
    AttentionResponse.model_validate(dict(generated_at=NOW, items=items))


@pytest.mark.parametrize("status,policy", [("churned", "full"), ("active", "excluded")])
def test_attention_hidden(status: str, policy: str) -> None:
    assert (
        build_attention([ASSESSMENT], [{**CLIENT, "status": status, "policy": policy}], NOW) == []
    )


def test_attention_recent_changes_cutoff_and_invalid_drivers() -> None:
    recent = {
        **ASSESSMENT,
        "risk_state": "stable",
        "state_since": "2026-09-19",
        "drivers": [dict(type="bad")],
    }
    assert build_attention([recent], [CLIENT], NOW)[0]["drivers"] == []
    assert build_attention([{**recent, "state_since": "2026-09-18"}], [CLIENT], NOW) == []
    assert build_attention([{**recent, "previous_risk_state": None}], [CLIENT], NOW) == []


def test_attention_limit_and_notice_order() -> None:
    clients = [
        {**CLIENT, "id": str(UUID(int=i + 100)), "name": f"Client {i:03}"} for i in range(155)
    ]
    rows = [{**ASSESSMENT, "client_id": c["id"], "drivers": []} for c in clients]
    assert len(build_attention(rows, clients, NOW)) == 150
    rows[2]["drivers"] = [
        dict(
            type="renewal_window", severity="low", basis="metadata", inputs=dict(days_to_notice=-1)
        )
    ]
    assert build_attention(rows, clients, NOW)[0]["client_id"] == clients[2]["id"]


def event(change: str, severity: str, at: str, index: int) -> dict[str, Any]:
    return dict(
        id=str(UUID(int=index + 100)),
        signal_id=str(UUID(int=4)),
        client_id=CID,
        change=change,
        severity=severity,
        as_of=at,
        created_at=at,
        signal=dict(
            type="delivery_complaint",
            family="delivery",
            basis="evidence",
            signal_evidence=[dict(evidence_id=EID)],
        ),
    )


def test_changes_all_kinds_and_baselines() -> None:
    baseline = {**ASSESSMENT, "as_of": "2026-09-25"}
    changed = {**ASSESSMENT, "as_of": "2026-09-26", "expansion_state": "hypothesis"}
    events = [
        event("opened", "low", "2026-09-25T00:00:00Z", 1),
        event("severity_changed", "high", "2026-09-26T00:00:00Z", 2),
        event("resolved", "high", "2026-09-27T00:00:00Z", 3),
        event("reopened", "high", "2026-09-28T00:00:00Z", 4),
    ]
    items = build_changes([changed, baseline], events[::-1], [CLIENT], "2026-09-26", NOW)
    assert [i["kind"] for i in items] == [
        "signal_opened",
        "signal_resolved",
        "risk_state_changed",
        "expansion_state_changed",
        "signal_escalated",
    ]
    assert [i["evidence_count"] for i in items] == [1, 0, 0, 0, 1]
    ChangesResponse.model_validate(dict(generated_at=NOW, since="2026-09-26", items=items))


def test_changes_lowering_then_raise_and_microseconds() -> None:
    events = [
        event("opened", "high", "2026-09-25T00:00:00Z", 1),
        event("severity_changed", "low", "2026-09-26T00:00:00.000001Z", 3),
        event("severity_changed", "medium", "2026-09-26T00:00:00.000002Z", 2),
    ]
    items = build_changes([], events[::-1], [CLIENT], "2026-09-26", NOW)
    assert len(items) == 1 and items[0]["severity"] == "medium"
    items = build_changes([], events, [{**CLIENT, "policy": "metadata_only"}], "2026-09-26", NOW)
    assert items[0]["evidence_count"] == 0


def test_changes_future_hidden_limit_and_initial_state() -> None:
    events = [event("opened", "low", "2026-09-26T00:00:00Z", i) for i in range(305)]
    assert len(build_changes([], events, [CLIENT], "2026-09-26", NOW)) == 300
    assert build_changes([], events, [{**CLIENT, "policy": "excluded"}], "2026-09-26", NOW) == []
    assert (
        build_changes(
            [], [event("opened", "low", "2026-10-04T00:00:00Z", 1)], [CLIENT], "2026-09-26", NOW
        )
        == []
    )
    assert (
        build_changes(
            [{**ASSESSMENT, "previous_risk_state": None}], [], [CLIENT], "2026-09-26", NOW
        )
        == []
    )


def test_client_detail_evidence_champion_history_and_caveat() -> None:
    detail = build_client_detail(
        CLIENT,
        ASSESSMENT,
        [ASSESSMENT, {**ASSESSMENT, "as_of": "2026-09-30", "previous_risk_state": "high"}],
        PARTS,
    )
    assert len(detail["state_history"]) == 1 and detail["state_history"][0]["from"] == "watch"
    assert detail["stakeholders"][0]["is_champion"] is True
    assert detail["assessment"]["caveats"] == [
        dict(code="participant_coverage", detail="Few participants.")
    ]
    for key in ("commitments", "goals", "needs"):
        assert detail[key][0]["evidence_ids"] == [EID]
    assert "PRIVATE BODY" not in str(detail)
    assert detail["unknowns"][0]["code"] == "budget_unknown"


def test_client_metadata_removes_content_and_unknown_inputs() -> None:
    detail = build_client_detail({**CLIENT, "policy": "metadata_only"}, ASSESSMENT, [], PARTS)
    assert all(detail[key] == [] for key in ("commitments", "goals", "needs"))
    assert len(detail["assessment"]["drivers"]) == 1
    assert detail["assessment"]["drivers"][0]["inputs"] == dict(
        days_to_notice=1, renewal_date="2026-11-01"
    )
    assert detail["assessment"]["drivers"][0]["evidence_ids"] == []
    assert "PRIVATE BODY" not in str(detail)


def test_list_nullable_and_history_limit() -> None:
    assert build_client_list_item(CLIENT, None)["assessment"] is None
    assert build_client_list_item(CLIENT, ASSESSMENT)["assessment"]["risk_state"] == "high"
    rows = [
        {**ASSESSMENT, "as_of": (NOW.date() - timedelta(days=i)).isoformat()} for i in range(25)
    ]
    assert len(build_state_history(rows)) == 20


def meeting() -> tuple[dict[str, Any], dict[str, Any]]:
    start = "2026-10-04T08:00:00Z"
    return dict(
        id=str(UUID(int=5)),
        calendar_event_id=str(UUID(int=6)),
        client_id=CID,
        starts_at=start,
        basis="evidence",
        content=dict(
            since_last_meeting=dict(records=4, last_held_at="2026-09-30T08:00:00+00:00"),
            open_commitments=[
                dict(
                    side="firm",
                    text="Synthetic promise",
                    due="2026-10-05",
                    late=True,
                    evidence_ids=[EID, "x"],
                )
            ],
            unresolved_complaints=[
                dict(subtype="delivery", text="Synthetic complaint", evidence_ids=[EID])
            ],
            stakeholder_changes=[dict(text="Synthetic change", evidence_ids=[EID])],
            renewal=dict(date="2026-11-01", days_to_notice=1),
            unknowns=[dict(code="budget_unknown", detail="Budget unstated")],
            questions=[
                dict(text="Ask about delivery", basis=dict(kind="complaint", ref=EID)),
                dict(text="Ask about budget", basis=dict(kind="unknown", ref="budget_unknown")),
            ],
        ),
    ), dict(id=str(UUID(int=6)), status="scheduled", starts_at=start)


def test_meeting_full_and_metadata() -> None:
    brief, calendar = meeting()
    items = build_meetings([brief], [calendar], [CLIENT], NOW)
    assert items[0]["starts_at"] == "2026-10-04T08:00:00.000Z"
    assert items[0]["open_commitments"][0]["evidence_ids"] == [EID]
    assert len(items[0]["questions"]) == 2
    MeetingsResponse.model_validate(dict(generated_at=NOW, items=items))
    meta = build_meetings([brief], [calendar], [{**CLIENT, "policy": "metadata_only"}], NOW)[0]
    assert (
        meta["basis"] == "metadata"
        and meta["open_commitments"]
        == meta["unresolved_complaints"]
        == meta["stakeholder_changes"]
        == []
    )
    assert len(meta["questions"]) == 1 and meta["questions"][0]["basis"]["kind"] == "unknown"
    assert EID not in str(meta)


@pytest.mark.parametrize(
    "field,value", [("status", "cancelled"), ("starts_at", "2026-10-05T08:00:00Z")]
)
def test_meeting_stale_event(field: str, value: str) -> None:
    brief, calendar = meeting()
    assert build_meetings([brief], [{**calendar, field: value}], [CLIENT], NOW) == []


def test_meeting_bad_sections_and_limit() -> None:
    brief, calendar = meeting()
    bad = deepcopy(brief)
    bad["content"].update(
        since_last_meeting=dict(records="bad"),
        open_commitments=[dict(text="")],
        questions=[dict(text=9)],
        renewal=dict(date="bad"),
    )
    item = build_meetings([bad], [calendar], [CLIENT], NOW)[0]
    assert item["since_last_meeting"] == dict(records=0, last_held_at=None)
    assert item["open_commitments"] == item["questions"] == [] and item["renewal"] is None
    assert build_meetings([{**brief, "content": []}], [calendar], [CLIENT], NOW) == []
    briefs = [{**brief, "id": str(UUID(int=i + 100))} for i in range(12)]
    assert len(build_meetings(briefs[::-1], [calendar], [CLIENT], NOW)) == 10
