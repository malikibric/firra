from datetime import UTC, date, datetime
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.routines import RoutinesRepo
from firra.db.types import WorkspaceId
from firra.routines.model import Decision

WS = WorkspaceId(UUID(int=1))
DAY = date(2026, 10, 5)


def fake_repo() -> tuple[RoutinesRepo, MagicMock]:
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    return RoutinesRepo(cast(ConnectionPool[Connection[DictRow]], pool)), conn


def test_claim_and_finish_are_scoped_and_clear_retry_state() -> None:
    repo, conn = fake_repo()
    conn.execute.return_value.fetchone.return_value = None
    assert repo.claim_run(workspace_id=WS, routine_id=UUID(int=2), scheduled_for=DAY) is None
    for sql, params in (call.args for call in conn.execute.call_args_list):
        assert params["workspace_id"] == WS
        assert "pre_meeting_brief" in sql
    update = conn.execute.call_args.args[0]
    assert "('queued','failed')" in update
    assert "finished_at = null" in update and "error_class = null" in update
    repo.finish_run(
        workspace_id=WS,
        run_id=UUID(int=3),
        status="failed",
        counts={"items": 0},
        error_class="ValueError",
    )
    params = conn.execute.call_args.args[1]
    assert params["workspace_id"] == WS and params["error_class"] == "ValueError"
    assert params["counts"].obj == {"items": 0}


def test_writer_skips_excluded_and_missing_assessments() -> None:
    repo, conn = fake_repo()
    decision = Decision(
        UUID(int=2), "state_change", "risk_fell", "fyi", None, (), frozenset(), 0, None, DAY
    )
    conn.execute.return_value.fetchone.side_effect = [None, {"policy": "full"}, None]
    assert repo.write_cards(
        workspace_id=WS, decisions=(decision, decision), run_id=UUID(int=3), as_of=DAY
    ) == {"written": 0, "skipped_no_assessment": 1}
    assert len(conn.execute.call_args_list) == 3


def test_due_unknown_timezone_is_countable_and_same_date_is_skipped() -> None:
    repo, conn = fake_repo()
    conn.execute.return_value.fetchall.return_value = [
        {
            "workspace_id": WS,
            "id": UUID(int=2),
            "kind": "morning_scan",
            "timezone": "unknown/zone",
            "last_date": None,
        },
        {
            "workspace_id": WS,
            "id": UUID(int=3),
            "kind": "commitment_watch",
            "timezone": "UTC",
            "last_date": DAY,
        },
    ]
    due = repo.due_routines(now=datetime(2026, 10, 5, 23, tzinfo=UTC))
    assert len(due) == 1
    assert due[0].routine_id == UUID(int=2) and due[0].unknown_timezone
    assert due[0].local_date == DAY and due[0].workspace_id == WS
    sql = conn.execute.call_args.args[0]
    assert "('morning_scan', 'commitment_watch')" in sql
