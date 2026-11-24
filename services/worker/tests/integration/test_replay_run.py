"""Replay run on local Postgres: CLI and task, idempotent, never writes signals (D8)."""

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from firra.app import create_app
from firra.assessment.__main__ import main as cli
from tests.conftest import LOCAL_SECRET, local_settings
from tests.integration.test_signals_facts_as_of import World
from tests.tokens import local_token

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]

REF = datetime(2026, 9, 30, 12, tzinfo=UTC)
START = (REF - timedelta(days=120)).date()


@pytest.fixture
def conn() -> Iterator[Db]:
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as c:
        yield c


def _client(w: World, name: str, **columns: Any) -> UUID:
    keys = ", ".join(["workspace_id", "name", *columns])
    marks = ", ".join(["%s"] * (2 + len(columns)))
    row = w.db.execute(
        f"insert into public.client ({keys}) values ({marks}) returning id",  # noqa: S608
        (w.ws, name, *columns.values()),
    ).fetchone()
    assert row is not None
    return UUID(str(row["id"]))


def _world(db: Db) -> dict[str, Any]:
    w = World(db, REF)
    # Acme (active): steady client mail over the whole window.
    for day in range(2, 200, 6):
        w.record(w.ana, REF - timedelta(days=day))
    # Lost: busy until ~70 days before churn, then quiet; churned 20 days ago.
    churned_at = REF - timedelta(days=20)
    lost = _client(w, "lost", status="churned", churned_at=churned_at)
    acme = w.client
    w.client = lost
    person = w._person("Lou", REF - timedelta(days=300))
    for day in range(90, 260, 4):
        w.record(person, REF - timedelta(days=day))
    w.client = acme
    db.execute(
        "insert into public.outcome (workspace_id, client_id, event, occurred_at, "
        "notice_at, first_known_at) "
        "values (%s, %s, 'churned', %s, %s, %s)",
        (
            w.ws,
            lost,
            churned_at.date(),
            (REF - timedelta(days=50)).date(),
            (REF - timedelta(days=55)).date(),
        ),
    )
    hidden = _client(w, "hidden", policy="excluded")
    return {"world": w, "acme": acme, "lost": lost, "hidden": hidden, "churned": churned_at}


def _rows(db: Db, ws: UUID, run: UUID) -> dict[UUID, list[date]]:
    rows = db.execute(
        "select client_id, as_of from public.account_assessment "
        "where workspace_id = %s and run_kind = 'replay' and replay_run_id = %s order by as_of",
        (ws, run),
    ).fetchall()
    out: dict[UUID, list[date]] = {}
    for r in rows:
        out.setdefault(r["client_id"], []).append(r["as_of"])
    return out


def test_replay_cli_and_task(
    conn: Db, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _world(conn)
    other = _world(conn)
    w: World = data["world"]
    monkeypatch.setenv("FIRRA_DATABASE_URL", local_settings().database_url.get_secret_value())
    assert (
        cli(["replay", "--workspace", str(w.ws), "--from", str(START), "--to", str(REF.date())])
        == 0
    )
    printed = json.loads(capsys.readouterr().out)
    run = UUID(printed["replay_run_id"])
    assert printed["totals"]["losses"] == 1 and printed["totals"]["retained"] == 1

    rows = _rows(conn, w.ws, run)
    assert set(rows) == {data["acme"], data["lost"]}, "excluded clients are never replayed"
    assert rows[data["acme"]][0] == START and rows[data["acme"]][-1] == REF.date()
    assert rows[data["lost"]][-1] == data["churned"].date(), "a churned client stops at churn"
    assert len(rows[data["acme"]]) == len(set(rows[data["acme"]]))

    stored = conn.execute(
        "select status::text as status, summary, rule_version, assessment_version, started_at, "
        "finished_at from public.replay_run where workspace_id = %s and id = %s",
        (w.ws, run),
    ).fetchone()
    assert stored is not None and stored["status"] == "succeeded"
    assert (
        stored["rule_version"] == "signals.v3" and stored["assessment_version"] == "assessment.v1"
    )
    lost = next(c for c in stored["summary"]["clients"] if c["client_id"] == str(data["lost"]))
    assert lost["notice_at"] == (REF - timedelta(days=50)).date().isoformat()

    signals = conn.execute(
        "select count(*)::int as n from public.signal where workspace_id = %s", (w.ws,)
    ).fetchone()
    assert signals is not None and signals["n"] == 0, "a replay never writes signals"

    # The same run again through the task: rows are replaced, not added.
    job = conn.execute(
        "insert into public.job (workspace_id, type, idempotency_key, payload) "
        "values (%s, 'assessment.replay', %s, %s::jsonb) returning id",
        (w.ws, f"replay-{uuid4()}", json.dumps({"replay_run_id": str(run)})),
    ).fetchone()
    assert job is not None
    with TestClient(create_app(local_settings())) as client:
        response = client.post(
            "/tasks/assessment.replay",
            headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
            json={
                "workspace_id": str(w.ws),
                "job_id": str(job["id"]),
                "idempotency_key": str(job["id"]),
                "type": "assessment.replay",
                "payload": {"replay_run_id": str(run)},
            },
        )
    assert response.json() == {"status": "succeeded"}
    assert _rows(conn, w.ws, run) == rows
    narratives = conn.execute(
        "select client_id, version, narrative from public.replay_narrative "
        "where workspace_id = %s and replay_run_id = %s",
        (w.ws, run),
    ).fetchall()
    assert {n["client_id"] for n in narratives} == {data["acme"], data["lost"]}
    assert all(n["version"] == 1 and "client_name" not in n["narrative"] for n in narratives)
    assert "narratives" not in stored["summary"]
    assert conn.execute(
        "select count(*)::int as n from public.account_assessment where workspace_id = %s",
        (other["world"].ws,),
    ).fetchone() == {"n": 0}, "another workspace is untouched"


def test_unknown_run_fails_permanently(conn: Db) -> None:
    w: World = _world(conn)["world"]
    missing = uuid4()
    job = conn.execute(
        "insert into public.job (workspace_id, type, idempotency_key, payload) "
        "values (%s, 'assessment.replay', %s, %s::jsonb) returning id",
        (w.ws, f"replay-{uuid4()}", json.dumps({"replay_run_id": str(missing)})),
    ).fetchone()
    assert job is not None
    with TestClient(create_app(local_settings())) as client:
        response = client.post(
            "/tasks/assessment.replay",
            headers={"Authorization": f"Bearer {local_token(LOCAL_SECRET)}"},
            json={
                "workspace_id": str(w.ws),
                "job_id": str(job["id"]),
                "idempotency_key": str(job["id"]),
                "type": "assessment.replay",
                "payload": {"replay_run_id": str(missing)},
            },
        )
    assert response.json()["status"] == "failed"
    row = conn.execute("select error_class from public.job where id = %s", (job["id"],)).fetchone()
    assert row == {"error_class": "replay_run_not_found"}


def test_report_files_from_a_replay_run(
    conn: Db,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Any,
) -> None:
    data = _world(conn)
    w: World = data["world"]
    monkeypatch.setenv("FIRRA_DATABASE_URL", local_settings().database_url.get_secret_value())
    args = ["replay", "--workspace", str(w.ws), "--from", str(START), "--to", str(REF.date())]
    assert cli(args) == 0
    run = json.loads(capsys.readouterr().out)["replay_run_id"]
    out = tmp_path / "report"
    assert cli(["report", "--workspace", str(w.ws), "--run", run, "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["acme.md", "index.md", "lost.md"]
    index = (out / "index.md").read_text()
    assert "| [lost](lost.md) |" in index and "| [acme](acme.md) |" in index
    assert "hidden" not in index
    assert (out / "lost.md").read_text().startswith("# lost — Churn Replay\n")

    lost_text = (out / "lost.md").read_text()
    assert f"The firm learned of the loss: {(REF - timedelta(days=55)).date()}" in lost_text
    assert f"Recorded notice date: {(REF - timedelta(days=50)).date()}" in lost_text
    assert f"Recorded churn date: {data['churned'].date()}" in lost_text

    # Reports using persisted narratives match the legacy builder byte for byte.
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    conn.execute(
        "delete from public.replay_narrative where workspace_id = %s and replay_run_id = %s",
        (w.ws, run),
    )
    assert cli(["report", "--workspace", str(w.ws), "--run", run, "--out", str(out)]) == 0
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
    assert cli(["narrate", "--workspace", str(w.ws), "--run", run]) == 0
    first = conn.execute(
        "select client_id, narrative from public.replay_narrative "
        "where workspace_id = %s and replay_run_id = %s order by client_id",
        (w.ws, run),
    ).fetchall()
    assert cli(["narrate", "--workspace", str(w.ws), "--run", run]) == 0
    assert (
        conn.execute(
            "select client_id, narrative from public.replay_narrative "
            "where workspace_id = %s and replay_run_id = %s order by client_id",
            (w.ws, run),
        ).fetchall()
        == first
    )
    assert cli(["narrate", "--workspace", str(uuid4()), "--run", run]) == 2

    # Unknown or unfinished runs are refused.
    assert cli(["report", "--workspace", str(w.ws), "--run", str(uuid4()), "--out", str(out)]) == 2


def test_report_reads_quotes_and_dates_of_verified_evidence(conn: Db) -> None:
    from firra.db.pool import create_pool_for
    from firra.db.replay import ReportRepo
    from firra.db.types import WorkspaceId

    w = World(conn, REF)
    record = w.record(w.ana, REF - timedelta(days=3))
    evidence = w.evidence(record)
    other = World(conn, REF)
    with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
        repo = ReportRepo(pool)
        found = repo.evidence(workspace_id=WorkspaceId(w.ws), ids=[evidence])
        assert found[evidence]["quote"] == "We are late again with this report"
        assert found[evidence]["occurred_at"] == REF - timedelta(days=3)
        assert repo.evidence(workspace_id=WorkspaceId(other.ws), ids=[evidence]) == {}
        assert repo.evidence(workspace_id=WorkspaceId(w.ws), ids=[]) == {}


def test_report_evidence_context_and_policy(conn: Db) -> None:
    from firra.db.pool import create_pool_for
    from firra.db.replay import ReportRepo
    from firra.db.types import WorkspaceId

    w = World(conn, REF)
    record = w.record(w.ana, REF)
    evidence = w.evidence(record)
    conn.execute(
        "update public.person set role_title = 'CMO' where workspace_id = %s and id = %s",
        (w.ws, w.ana),
    )
    conn.execute(
        "update public.source_record set metadata = jsonb_build_object('subject', %s::text) "
        "where workspace_id = %s and id = %s",
        ("  " + "s" * 130 + "  ", w.ws, record),
    )
    with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
        repo = ReportRepo(pool)
        found = repo.evidence(workspace_id=WorkspaceId(w.ws), ids=[evidence])[evidence]
        assert found["author_name"] == "Ana" and found["author_role"] == "CMO"
        assert found["subject"] == "s" * 120 and found["source_ref"] == str(record)[:8]
        conn.execute(
            "update public.person set side = 'firm', client_id = null "
            "where workspace_id = %s and id = %s",
            (w.ws, w.ana),
        )
        conn.execute(
            "update public.source_record set metadata = '{}' where workspace_id = %s and id = %s",
            (w.ws, record),
        )
        found = repo.evidence(workspace_id=WorkspaceId(w.ws), ids=[evidence])[evidence]
        assert found["author_name"] == "our team" and found["author_role"] is None
        assert found["subject"] is None and found["deep_link"] is None
        conn.execute(
            "update public.source_record set author_person_id = null "
            "where workspace_id = %s and id = %s",
            (w.ws, record),
        )
        assert (
            repo.evidence(workspace_id=WorkspaceId(w.ws), ids=[evidence])[evidence]["author_name"]
            is None
        )
        conn.execute(
            "update public.client set policy = 'metadata_only' where workspace_id = %s and id = %s",
            (w.ws, w.client),
        )
        assert repo.evidence(workspace_id=WorkspaceId(w.ws), ids=[evidence]) == {}
        conn.execute(
            "update public.client set policy = 'excluded' where workspace_id = %s and id = %s",
            (w.ws, w.client),
        )
        assert repo.evidence(workspace_id=WorkspaceId(w.ws), ids=[evidence]) == {}


def test_narrative_metadata_policy_and_atomic_finish(
    conn: Db, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from firra.db.pool import create_pool_for
    from firra.db.replay import ReplayRepo
    from firra.db.types import WorkspaceId

    data = _world(conn)
    w: World = data["world"]
    conn.execute(
        "update public.client set policy = 'metadata_only' where workspace_id = %s and id = %s",
        (w.ws, data["acme"]),
    )
    monkeypatch.setenv("FIRRA_DATABASE_URL", local_settings().database_url.get_secret_value())
    assert (
        cli(["replay", "--workspace", str(w.ws), "--from", str(START), "--to", str(REF.date())])
        == 0
    )
    run = UUID(json.loads(capsys.readouterr().out)["replay_run_id"])
    row = conn.execute(
        "select narrative from public.replay_narrative "
        "where workspace_id = %s and replay_run_id = %s and client_id = %s",
        (w.ws, run, data["acme"]),
    ).fetchone()
    assert row is not None

    def check(value: Any) -> None:
        if isinstance(value, dict):
            if "basis" in value:
                assert value["basis"] == "metadata" and value["evidence_ids"] == []
            for nested in value.values():
                check(nested)
        elif isinstance(value, list):
            for nested in value:
                check(nested)

    check(row["narrative"])
    before = conn.execute(
        "select narrative from public.replay_narrative "
        "where workspace_id = %s and replay_run_id = %s order by client_id",
        (w.ws, run),
    ).fetchall()
    with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
        repo = ReplayRepo(pool)
        with pytest.raises(psycopg.errors.CheckViolation):
            repo.finish(
                workspace_id=WorkspaceId(w.ws), run_id=run, summary=cast(dict[str, Any], [])
            )
    assert (
        conn.execute(
            "select narrative from public.replay_narrative "
            "where workspace_id = %s and replay_run_id = %s order by client_id",
            (w.ws, run),
        ).fetchall()
        == before
    )
