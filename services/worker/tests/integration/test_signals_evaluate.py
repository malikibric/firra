"""signals.evaluate end to end on the local Postgres: facts in, signal rows out, per workspace."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from firra.app import create_app
from tests.conftest import LOCAL_SECRET, local_settings
from tests.tokens import local_token

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]

AS_OF = date(2026, 9, 30)
REF = datetime(2026, 9, 30, 23, 59, 59, 999999, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Db]:
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as conn:
        yield conn


def _id(row: dict[str, Any] | None) -> UUID:
    assert row is not None
    return UUID(str(row["id"]))


def _workspace(db: Db, name: str) -> dict[str, Any]:
    ws = _id(
        db.execute(
            "insert into public.workspace (name) values (%s) returning id", (name,)
        ).fetchone()
    )
    ids: dict[str, Any] = {"ws": ws}
    ids["conn"] = _id(
        db.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label) "
            "values (%s, 'email_upload', 'upload', 'x') returning id",
            (ws,),
        ).fetchone()
    )
    for key, policy in (("full", "full"), ("meta", "metadata_only")):
        ids[key] = _id(
            db.execute(
                "insert into public.client (workspace_id, name, policy) values (%s, %s, %s) "
                "returning id",
                (ws, key, policy),
            ).fetchone()
        )
        ids[f"{key}_person"] = _id(
            db.execute(
                "insert into public.person (workspace_id, client_id, side) "
                "values (%s, %s, 'client') returning id",
                (ws, ids[key]),
            ).fetchone()
        )
    return ids


def _message(db: Db, ids: dict[str, Any], client: str, tag: str, days_ago: int) -> UUID:
    return _id(
        db.execute(
            "insert into public.source_record (workspace_id, connection_id, provider, external_id, "
            "kind, client_id, policy_at_ingest, thread_key, occurred_at, author_person_id, "
            "direction, participants, body_clean, processing_state) "
            "values (%s, %s, 'email_upload', %s, 'email', %s, %s, %s, %s, %s, 'in', %s, %s, %s) "
            "returning id",
            (
                ids["ws"],
                ids["conn"],
                f"{client}-{tag}",
                ids[client],
                "full" if client == "full" else "metadata_only",
                f"{client}-{tag}",
                REF - timedelta(days=days_ago, hours=6),
                ids[f"{client}_person"],
                [ids[f"{client}_person"]],
                "We are late again with this report." if client == "full" else None,
                "done",
            ),
        ).fetchone()
    )


def _negative_tone(db: Db, ids: dict[str, Any], record: UUID) -> UUID:
    with db.transaction():
        extraction = _id(
            db.execute(
                "insert into public.extraction (workspace_id, source_record_id, client_id, kind, "
                "payload, model, prompt_version, processing_version, item_index) "
                "values (%s, %s, %s, 'tone', '{\"tone\": \"negative\"}', 'm', 'extract.v1', 1, 0) "
                "returning id",
                (ids["ws"], record, ids["full"]),
            ).fetchone()
        )
        evidence = _id(
            db.execute(
                "insert into public.evidence (workspace_id, source_record_id, client_id, quote, "
                "locator) values (%s, %s, %s, 'We are late again', '{\"start\": 0, \"end\": 17}') "
                "returning id",
                (ids["ws"], record, ids["full"]),
            ).fetchone()
        )
        db.execute(
            "insert into public.extraction_evidence (workspace_id, extraction_id, evidence_id, "
            "client_id) values (%s, %s, %s, %s)",
            (ids["ws"], extraction, evidence, ids["full"]),
        )
    return evidence


@pytest.fixture
def world(db: Db) -> Iterator[dict[str, Any]]:
    a = _workspace(db, "signals-a")
    b = _workspace(db, "signals-b")
    # Client "full": six messages in the last 30 days, three of them negative; a clean baseline.
    negatives: list[UUID] = []
    for n, day in enumerate((2, 5, 8, 12, 18, 25)):
        record = _message(db, a, "full", f"r{n}", day)
        if n % 2 == 0:
            negatives.append(_negative_tone(db, a, record))
    for n, day in enumerate((40, 70, 100)):
        _message(db, a, "full", f"b{n}", day)
    # Client "meta" (metadata_only): thirteen messages 32..116 days ago and silence since.
    for n in range(13):
        _message(db, a, "meta", f"b{n}", 32 + n * 7)
    a["negatives"] = negatives
    # Workspace B holds an unresolved signal of its own that A's run must never touch.
    a_signal = db.execute(
        "insert into public.signal (workspace_id, client_id, type, severity, occurred_at, "
        "rule_version, basis, inputs, dedupe_key) values (%s, %s, 'reduced_engagement', 'low', "
        "now(), 'old', 'metadata', '{}', 'volume') returning id",
        (b["ws"], b["meta"]),
    ).fetchone()
    b["signal"] = _id(a_signal)
    yield {"a": a, "b": b}
    for ws in (a["ws"], b["ws"]):
        db.execute("delete from public.workspace where id = %s", (ws,))


def _run(db: Db, ws: UUID) -> dict[str, Any]:
    job = _id(
        db.execute(
            "insert into public.job (workspace_id, type, idempotency_key, payload) "
            "values (%s, 'signals.evaluate', %s, %s) returning id",
            (ws, f"signals-{uuid4()}", '{"trigger": "manual", "as_of": "2026-09-30"}'),
        ).fetchone()
    )
    app = create_app(local_settings())
    with TestClient(app) as client:
        response = client.post(
            "/tasks/signals.evaluate",
            headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
            json={
                "workspace_id": str(ws),
                "job_id": str(job),
                "idempotency_key": "k",
                "type": "signals.evaluate",
                "payload": {"trigger": "manual", "as_of": AS_OF.isoformat()},
            },
        )
    assert response.json() == {"status": "succeeded"}
    row = db.execute("select progress from public.job where id = %s", (job,)).fetchone()
    assert row is not None
    progress: dict[str, Any] = row["progress"]
    return progress


def _signals(db: Db, ws: UUID) -> dict[tuple[str, str], dict[str, Any]]:
    rows = db.execute(
        "select s.id, s.client_id, s.type, s.severity::text as severity, s.basis::text as basis, "
        "s.resolved_at, s.rule_version, "
        "(select count(*) from public.signal_evidence e where e.signal_id = s.id)::int as links "
        "from public.signal s where s.workspace_id = %s",
        (ws,),
    ).fetchall()
    return {(str(r["client_id"]), r["type"]): r for r in rows}


def test_signals_are_written_updated_and_resolved_per_client(db: Db, world: dict[str, Any]) -> None:
    a, b = world["a"], world["b"]
    progress = _run(db, a["ws"])
    assert progress["clients_evaluated"] == 2 and progress["clients_failed"] == 0
    assert progress["signals_by_type"] == {
        "tone_decline": 1,
        "reduced_engagement": 1,
        "client_initiation_drop": 1,
    }

    rows = _signals(db, a["ws"])
    tone = rows[(str(a["full"]), "tone_decline")]
    assert (tone["severity"], tone["basis"], tone["links"], tone["resolved_at"]) == (
        "high",
        "evidence",
        3,
        None,
    )
    engagement = rows[(str(a["meta"]), "reduced_engagement")]
    assert (engagement["severity"], engagement["basis"], engagement["links"]) == (
        "high",
        "metadata",
        0,
    )
    assert (str(a["full"]), "reduced_engagement") not in rows

    again = _run(db, a["ws"])
    assert again["signals_resolved"] == 0
    rows_again = _signals(db, a["ws"])
    assert {k: v["id"] for k, v in rows_again.items()} == {k: v["id"] for k, v in rows.items()}, (
        "a second run updates in place"
    )

    # The negative tone disappears (e.g. reprocessing dropped the quotes): the signal is resolved.
    db.execute("delete from public.evidence where id = any(%s)", (a["negatives"],))
    third = _run(db, a["ws"])
    assert third["signals_resolved"] == 1
    assert _signals(db, a["ws"])[(str(a["full"]), "tone_decline")]["resolved_at"] is not None

    untouched = db.execute(
        "select resolved_at, rule_version from public.signal where id = %s", (b["signal"],)
    ).fetchone()
    assert untouched is not None
    assert (untouched["resolved_at"], untouched["rule_version"]) == (None, "old"), (
        "another workspace's signals are never touched"
    )


def test_excluded_clients_are_not_evaluated(db: Db, world: dict[str, Any]) -> None:
    a = world["a"]
    db.execute("update public.client set policy = 'excluded' where id = %s", (a["full"],))
    progress = _run(db, a["ws"])
    assert progress["clients_evaluated"] == 1
    assert (str(a["full"]), "tone_decline") not in _signals(db, a["ws"])


# --- live assessment path (T21) ---------------------------------------------------------


def _run_on(db: Db, ws: UUID, as_of: date) -> dict[str, Any]:
    payload = {"trigger": "manual", "as_of": as_of.isoformat()}
    job = _id(
        db.execute(
            "insert into public.job (workspace_id, type, idempotency_key, payload) "
            "values (%s, 'signals.evaluate', %s, %s::jsonb) returning id",
            (ws, f"signals-{uuid4()}", '{"trigger": "manual"}'),
        ).fetchone()
    )
    with TestClient(create_app(local_settings())) as client:
        response = client.post(
            "/tasks/signals.evaluate",
            headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
            json={
                "workspace_id": str(ws),
                "job_id": str(job),
                "idempotency_key": str(job),
                "type": "signals.evaluate",
                "payload": payload,
            },
        )
    assert response.json() == {"status": "succeeded"}
    row = db.execute("select progress from public.job where id = %s", (job,)).fetchone()
    assert row is not None
    progress: dict[str, Any] = row["progress"]
    return progress


def _assessments(db: Db, ws: UUID) -> dict[tuple[str, date], dict[str, Any]]:
    rows = db.execute(
        "select a.id, a.client_id, a.as_of, a.risk_state::text as risk, "
        "a.previous_risk_state::text as previous, a.state_since, a.play, a.drivers, a.caveats, "
        "a.data, (select count(*) from public.assessment_evidence e "
        "          where e.assessment_id = a.id)::int as evidence, "
        "(select count(*) from public.assessment_signal s "
        "  where s.assessment_id = a.id)::int as signals "
        "from public.account_assessment a where a.workspace_id = %s and a.run_kind = 'live'",
        (ws,),
    ).fetchall()
    return {(str(r["client_id"]), r["as_of"]): r for r in rows}


def _events(db: Db, ws: UUID) -> list[tuple[str, str, str]]:
    rows = db.execute(
        "select s.type, e.change::text as change, e.severity::text as severity "
        "from public.signal_event e join public.signal s on s.id = e.signal_id "
        "where e.workspace_id = %s order by e.created_at, s.type",
        (ws,),
    ).fetchall()
    return [(r["type"], r["change"], r["severity"]) for r in rows]


def test_live_assessments_history_and_hysteresis(db: Db, world: dict[str, Any]) -> None:
    a, b = world["a"], world["b"]
    first_day, next_week, week_after = date(2026, 9, 30), date(2026, 10, 7), date(2026, 10, 14)
    full, meta = str(a["full"]), str(a["meta"])

    progress = _run_on(db, a["ws"], first_day)
    assert progress["assessments_by_state"] == {"high": 2}
    assert sorted(_events(db, a["ws"])) == [
        ("client_initiation_drop", "opened", "medium"),
        ("reduced_engagement", "opened", "high"),
        ("tone_decline", "opened", "high"),
    ]
    rows = _assessments(db, a["ws"])
    assert set(rows) == {(full, first_day), (meta, first_day)}
    full_row, meta_row = rows[(full, first_day)], rows[(meta, first_day)]
    assert (full_row["risk"], full_row["previous"], full_row["state_since"]) == (
        "high",
        None,
        first_day,
    )
    assert full_row["evidence"] == 3 and full_row["signals"] == 1
    assert full_row["drivers"][0]["type"] == "tone_decline"
    assert full_row["drivers"][0]["signal_id"] is not None
    assert meta_row["evidence"] == 0 and meta_row["signals"] == 2
    assert "metadata_only" in [c["code"] for c in meta_row["caveats"]]

    # A second run on the same day replaces the day's row and records no new change.
    _run_on(db, a["ws"], first_day)
    again = _assessments(db, a["ws"])
    assert {k: v["id"] for k, v in again.items()} == {k: v["id"] for k, v in rows.items()}
    assert len(_events(db, a["ws"])) == 3

    # The negative tone disappears: the signal resolves, but the state waits one run.
    db.execute("delete from public.evidence where id = any(%s)", (a["negatives"],))
    _run_on(db, a["ws"], next_week)
    held = _assessments(db, a["ws"])[(full, next_week)]
    assert (held["risk"], held["previous"], held["state_since"]) == ("high", "high", first_day)
    assert held["data"]["computed_risk"] == "stable" and held["data"]["below_runs"] == 1
    assert ("tone_decline", "resolved", "high") in _events(db, a["ws"])

    _run_on(db, a["ws"], week_after)
    dropped = _assessments(db, a["ws"])[(full, week_after)]
    assert (dropped["risk"], dropped["previous"], dropped["state_since"]) == (
        "stable",
        "high",
        week_after,
    )
    assert dropped["play"] == "monitor"

    assert _assessments(db, b["ws"]) == {}, "another workspace gets no assessments"
