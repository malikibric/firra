from uuid import UUID

from tests.unit.routines.test_repository import DAY, WS, fake_repo

from firra.db.routines import STALE_RUN_MINUTES


def test_stale_running_claim_uses_strict_fifteen_minute_threshold() -> None:
    repo, conn = fake_repo()
    conn.execute.return_value.fetchone.return_value = None
    repo.claim_run(workspace_id=WS, routine_id=UUID(int=2), scheduled_for=DAY)
    sql, params = conn.execute.call_args.args
    assert STALE_RUN_MINUTES == params["stale_minutes"] == 15
    assert "rr.status = 'running'" in sql
    assert "rr.started_at < now() - make_interval(mins => %(stale_minutes)s)" in sql
