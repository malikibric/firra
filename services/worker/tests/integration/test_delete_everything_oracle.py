"""Independent oracle for DELETE /local/data ("Delete everything", L8-03o).

Written from the original task spec before the L8-03
implementation exists, at the HTTP level (TestClient) plus SQL assertions.
On ``main`` the route is not mounted yet, so the whole module skips via the
one ``delete_route_present`` probe below; once the lead's route lands the
probe turns the suite on automatically.

Seeded fixture (all synthetic, greppable markers below): clients of every
policy (full/excluded/metadata_only), a domain, a service, gmail + slack +
upload connections (the slack one with a channel scope), client and firm
people with email/slack identities, mail and chat records with bodies,
an extraction with evidence, a chat evidence row, an evidence-basis signal,
a live and a replay assessment, a decision card with feedback, an outcome,
a queued and a running job, an audit row, a succeeded replay run, a routine
with a succeeded run, and an llm_usage row.

Fake injection (see Report for the seams): provider revoke calls are
recorded, never sent — ``firra.connectors.slack.auth.revoke`` is replaced by
a recording fake (dead on one token), and ``httpx.Client.post`` is wrapped so
only Google-revoke traffic is faked (everything else delegates, so TestClient
traffic is untouched). ``ControlServer.send_secret_delete`` is wrapped with a
recorder in case the route threads a control server through.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from tests.integration.conftest import LOCAL_AUTH

QUOTE_FULL = "Oracle deletion full quote about the slipped onboarding timeline verbatim"
QUOTE_CHAT = "Oracle deletion chat quote about the late media plan verbatim"
DOMAIN = "oracle-deletion.example"
ADDRESS = "oracle.deletion@oracle-deletion.example"
SLACK_GOOD_TOKEN = "xoxp-oracle-deletion-good-token"
SLACK_DEAD_TOKEN = "xoxp-oracle-deletion-dead-token"
GOOGLE_GOOD_TOKEN = "oracle-deletion-google-good-token"
GOOGLE_DEAD_TOKEN = "oracle-deletion-google-dead-token"


def delete_route_present() -> bool:
    """One probe: True once a local route serves DELETE /local/data (any module)."""
    repo = Path(__file__).resolve().parents[4]
    routes = repo / "services" / "worker" / "firra" / "local" / "routes"
    assert routes.is_dir(), f"oracle probe path moved: {routes}"
    return any('"/data"' in f.read_text() for f in routes.glob("*.py"))


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not delete_route_present(), reason="L8-03 DELETE /local/data not mounted"),
]


def _tables(db: Any) -> list[str]:
    rows = db.execute(
        "select tablename from pg_tables where schemaname = 'public' order by 1"
    ).fetchall()
    return [r["tablename"] for r in rows]


def _scalar(db: Any, sql: str) -> Any:
    row = db.execute(sql).fetchone()
    assert row is not None
    return row["n"] if isinstance(row, dict) else row[0]


def _total_rows(db: Any) -> dict[str, int]:
    # Table names come from pg_tables, never from input.
    return {
        table: db.execute(
            psycopg.sql.SQL("select count(*) as n from public.{}").format(
                psycopg.sql.Identifier(table)
            )
        ).fetchone()["n"]
        for table in _tables(db)
    }


@pytest.fixture(scope="module")
def seeded(local_app: tuple[Any, Any, str]) -> dict[str, Any]:
    """Rich synthetic workspace; returns ids and the pre-delete per-table counts."""
    _, ctx, conninfo = local_app
    ws = ctx.identity.workspace_id
    ids = {name: uuid4() for name in ("full", "excluded", "metadata")}
    out: dict[str, Any] = {"ids": ids}
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        db.execute(
            "insert into public.client (id, workspace_id, name, policy) values "
            "(%s,%s,'Oracle Deletion Full','full'),"
            "(%s,%s,'Oracle Deletion Excluded','excluded'),"
            "(%s,%s,'Oracle Deletion Meta','metadata_only')",
            (ids["full"], ws, ids["excluded"], ws, ids["metadata"], ws),
        )
        db.execute(
            "insert into public.client_domain (workspace_id, client_id, domain) values (%s,%s,%s)",
            (ws, ids["full"], DOMAIN),
        )
        db.execute(
            "insert into public.workspace_service (workspace_id, name) values (%s,'Oracle SEO')",
            (ws,),
        )
        gmail = db.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label,"
            " external_account_id, status) values (%s,'gmail','user','oracle gmail',"
            "'oracle.gmail@oracle-deletion.example','active') returning id",
            (ws,),
        ).fetchone()
        slack = db.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label,"
            " external_account_id, status) values (%s,'slack','user','oracle slack',"
            "'TORACLE1','active') returning id",
            (ws,),
        ).fetchone()
        upload = db.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label,"
            " status) values (%s,'email_upload','upload','oracle upload','active') returning id",
            (ws,),
        ).fetchone()
        assert gmail is not None and slack is not None and upload is not None
        db.execute(
            "insert into public.connection_scope (workspace_id, connection_id, kind,"
            " external_id, client_id, include) values (%s,%s,'channel','C123',%s,true)",
            (ws, slack["id"], ids["full"]),
        )
        client_person = db.execute(
            "insert into public.person (workspace_id, client_id, side, display_name)"
            " values (%s,%s,'client','Oracle Client Contact') returning id",
            (ws, ids["full"]),
        ).fetchone()
        firm_person = db.execute(
            "insert into public.person (workspace_id, client_id, side, display_name)"
            " values (%s,%s,'firm','Oracle Founder') returning id",
            (ws, ids["full"]),
        ).fetchone()
        assert client_person is not None and firm_person is not None
        db.execute(
            "insert into public.person_identity (workspace_id, person_id, kind, value)"
            " values (%s,%s,'email',%s),(%s,%s,'slack_id','UORACLE1')",
            (ws, client_person["id"], ADDRESS, ws, client_person["id"]),
        )
        mail = db.execute(
            "insert into public.source_record (workspace_id, connection_id, provider,"
            " external_id, kind, client_id, policy_at_ingest, occurred_at, direction,"
            " author_person_id, word_count, body_clean, metadata)"
            " values (%s,%s,'gmail',%s,'email',%s,'full',now(),'in',%s,10,%s,%s) returning id",
            (
                ws,
                gmail["id"],
                str(uuid4()),
                ids["full"],
                client_person["id"],
                QUOTE_FULL,
                json.dumps({"channel": "mail"}),
            ),
        ).fetchone()
        chat = db.execute(
            "insert into public.source_record (workspace_id, connection_id, provider,"
            " external_id, kind, client_id, policy_at_ingest, occurred_at, direction,"
            " author_person_id, word_count, body_clean, metadata)"
            " values (%s,%s,'slack',%s,'chat',%s,'full',now(),'in',%s,9,%s,%s) returning id",
            (
                ws,
                slack["id"],
                str(uuid4()),
                ids["full"],
                client_person["id"],
                QUOTE_CHAT,
                json.dumps({"channel_id": "C123"}),
            ),
        ).fetchone()
        assert mail is not None and chat is not None
        # Records of the non-full clients: nothing is exempt from deletion (spec 5).
        db.execute(
            "insert into public.source_record (workspace_id, connection_id, provider,"
            " external_id, kind, client_id, policy_at_ingest, occurred_at, direction,"
            " word_count, metadata) values (%s,%s,'gmail',%s,'email',%s,"
            "'metadata_only',now(),'in',4,%s),(%s,%s,'gmail',%s,'email',%s,"
            "'full',now(),'in',4,%s)",
            (
                ws,
                gmail["id"],
                str(uuid4()),
                ids["metadata"],
                json.dumps({"channel": "mail"}),
                ws,
                gmail["id"],
                str(uuid4()),
                ids["excluded"],
                json.dumps({"channel": "mail"}),
            ),
        )
        with db.transaction():
            extraction = db.execute(
                "insert into public.extraction (workspace_id, source_record_id, client_id,"
                " kind, payload, model, prompt_version, processing_version, item_index)"
                " values (%s,%s,%s,'complaint',%s,'oracle','extract.v6',1,0) returning id",
                (ws, mail["id"], ids["full"], json.dumps({"subtype": "delay"})),
            ).fetchone()
            assert extraction is not None
            mail_evidence = db.execute(
                "insert into public.evidence (workspace_id, source_record_id, client_id,"
                " quote, locator) values (%s,%s,%s,%s,%s) returning id",
                (ws, mail["id"], ids["full"], QUOTE_FULL, json.dumps({"start": 0, "end": 10})),
            ).fetchone()
            assert mail_evidence is not None
            db.execute(
                "insert into public.extraction_evidence (workspace_id, extraction_id,"
                " evidence_id, client_id) values (%s,%s,%s,%s)",
                (ws, extraction["id"], mail_evidence["id"], ids["full"]),
            )
        chat_evidence = db.execute(
            "insert into public.evidence (workspace_id, source_record_id, client_id,"
            " quote, locator) values (%s,%s,%s,%s,%s) returning id",
            (ws, chat["id"], ids["full"], QUOTE_CHAT, json.dumps({"start": 0, "end": 9})),
        ).fetchone()
        assert chat_evidence is not None
        # An evidence-basis signal must cite evidence at commit (deferred trigger),
        # so the row and its link go in one transaction.
        with db.transaction():
            signal = db.execute(
                "insert into public.signal (workspace_id, client_id, type, severity,"
                " occurred_at, rule_version, basis, dedupe_key) values (%s,%s,"
                "'delivery_complaint','medium',now(),'signals.v3','evidence','delay')"
                " returning id",
                (ws, ids["full"]),
            ).fetchone()
            assert signal is not None
            db.execute(
                "insert into public.signal_evidence (workspace_id, signal_id, evidence_id,"
                " client_id) values (%s,%s,%s,%s)",
                (ws, signal["id"], mail_evidence["id"], ids["full"]),
            )
        db.execute(
            "insert into public.account_assessment (workspace_id, client_id, as_of,"
            " run_kind, assessment_version, rule_version, risk_state, previous_risk_state,"
            " state_since, expansion_state, strength, play) values (%s,%s,current_date,"
            " 'live','assess.v1','signals.v3','watch','stable',current_date,'none',"
            "'strong','protect')",
            (ws, ids["full"]),
        )
        replay = db.execute(
            "insert into public.replay_run (workspace_id, status, window_start, window_end,"
            " step_days, assessment_version, rule_version) values (%s,'succeeded',"
            " current_date - 84, current_date, 7,'assess.v1','signals.v3') returning id",
            (ws,),
        ).fetchone()
        assert replay is not None
        db.execute(
            "insert into public.account_assessment (workspace_id, client_id, as_of,"
            " run_kind, replay_run_id, assessment_version, rule_version, risk_state,"
            " previous_risk_state, state_since, expansion_state, strength, play)"
            " values (%s,%s,current_date - 7,'replay',%s,'assess.v1','signals.v3',"
            "'elevated','watch',current_date - 7,'none','strong','protect')",
            (ws, ids["full"], replay["id"]),
        )
        # A decision card must cite evidence at commit (deferred trigger).
        with db.transaction():
            card = db.execute(
                "insert into public.decision_card (workspace_id, client_id, week, kind, title,"
                " body, locale, score, priority, inputs, model, prompt_version, dedupe_key,"
                " play, facts, interpretation, recommendation, strength, status)"
                " values (%s,%s,date_trunc('week', now())::date,'risk',"
                "'Oracle deletion card','Oracle deletion body','en',7,1,%s,'oracle',"
                "'cards.v1','oracle-card','protect',%s,'Oracle says act',"
                "'Oracle says do this','strong','new') returning id",
                (ws, ids["full"], json.dumps({}), json.dumps([])),
            ).fetchone()
            assert card is not None
            db.execute(
                "insert into public.decision_evidence (workspace_id, decision_card_id,"
                " evidence_id, client_id) values (%s,%s,%s,%s)",
                (ws, card["id"], mail_evidence["id"], ids["full"]),
            )
        db.execute(
            "insert into public.decision_feedback (workspace_id, client_id, decision_card_id,"
            " user_id, verdict) values (%s,%s,%s,%s,'useful')",
            (ws, ids["full"], card["id"], ctx.identity.user_id),
        )
        db.execute(
            "insert into public.outcome (workspace_id, client_id, event, occurred_at,"
            " source) values (%s,%s,'expanded',now(),'user')",
            (ws, ids["full"]),
        )
        queued = db.execute(
            "insert into public.job (workspace_id, type, status, idempotency_key,"
            " payload) values (%s,'sync.gmail','queued',%s,%s) returning id",
            (ws, f"oracle-queued-{uuid4()}", json.dumps({"connection_id": str(gmail["id"])})),
        ).fetchone()
        running = db.execute(
            "insert into public.job (workspace_id, type, status, idempotency_key,"
            " payload, started_at) values (%s,'process.records','running',%s,%s,now())"
            " returning id",
            (ws, f"oracle-running-{uuid4()}", json.dumps({})),
        ).fetchone()
        assert queued is not None and running is not None
        db.execute(
            "insert into public.llm_usage (workspace_id, job_id, model, input_tokens,"
            " output_tokens, cost_usd) values (%s,%s,'oracle',10,5,0.01)",
            (ws, queued["id"]),
        )
        db.execute(
            "insert into public.audit_log (workspace_id, actor_type, action) values"
            " (%s,'system','oracle.seed')",
            (ws,),
        )
        routine = db.execute(
            "insert into public.routine (workspace_id, kind) values (%s,'morning_scan')"
            " returning id",
            (ws,),
        ).fetchone()
        assert routine is not None
        db.execute(
            "insert into public.routine_run (workspace_id, routine_id, scheduled_for,"
            " status, counts) values (%s,%s,current_date,'succeeded',%s)",
            (ws, routine["id"], json.dumps({"items": 1})),
        )
        out["pre_counts"] = _total_rows(db)
        out["applied_migrations"] = _scalar(
            db, "select count(*) as n from firra_local.applied_migration"
        )
        out["auth_users"] = _scalar(db, "select count(*) as n from auth.users")
    assert out["auth_users"] >= 1, "local owner row must exist before deletion"
    assert out["pre_counts"].get("client", 0) >= 3
    return out


class _Recorder:
    """Records provider-revoke attempts; dead on tokens containing 'dead'."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def slack_revoke(self, client: httpx.Client, token: str) -> None:
        del client
        self.calls.append(("slack", token))
        if "dead" in token:
            raise httpx.ConnectError("oracle dead token")

    def google_post(
        self, real_post: Any, client: httpx.Client, url: str, **kwargs: Any
    ) -> httpx.Response:
        if "oauth2.googleapis.com" not in str(url):
            delegated: httpx.Response = real_post(url, **kwargs)
            return delegated
        content = kwargs.get("content", b"")
        if isinstance(content, dict):
            content = str(content)
        token = content.decode() if isinstance(content, bytes) else str(content)
        self.calls.append(("google", token))
        if "dead" in token:
            raise httpx.ConnectError("oracle dead token")
        request = httpx.Request("POST", str(url))
        return httpx.Response(200, content=b"{}", request=request)


@pytest.fixture()
def revoke_fakes(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    import firra.connectors.slack.auth as slack_auth

    monkeypatch.setattr(slack_auth, "revoke", recorder.slack_revoke)
    real_post = httpx.Client.post

    def guarded_post(client: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
        result: httpx.Response = recorder.google_post(real_post, client, url, **kwargs)
        return result

    monkeypatch.setattr(httpx.Client, "post", guarded_post)
    return recorder


@pytest.fixture()
def secret_delete_recorder(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records secret_delete names if the route threads a ControlServer through."""
    from firra.local.control import ControlServer

    names: list[str] = []
    real = ControlServer.send_secret_delete

    def recording(self: ControlServer, name: str) -> None:
        names.append(name)
        return real(self, name)

    monkeypatch.setattr(ControlServer, "send_secret_delete", recording)
    return names


@pytest.fixture()
def captured_logs() -> Any:
    """All logging from the first record on; searched for leaks after deletion."""

    class Capture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.lines: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.lines.append(record.getMessage())

    capture = Capture()
    root = logging.getLogger()
    root.addHandler(capture)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        yield capture
    finally:
        root.setLevel(old_level)
        root.removeHandler(capture)


NEEDLES = (
    QUOTE_FULL,
    QUOTE_CHAT,
    ADDRESS,
    DOMAIN,
    "Oracle Deletion",
    SLACK_GOOD_TOKEN,
    SLACK_DEAD_TOKEN,
    GOOGLE_GOOD_TOKEN,
    GOOGLE_DEAD_TOKEN,
)


def test_confirm_required_422_and_nothing_changes(
    local_app: tuple[Any, Any, str], seeded: dict[str, Any]
) -> None:
    """Spec 1: wrong or missing confirm -> 422 invalid_request, nothing changes."""
    client, _, conninfo = local_app
    for body in ({}, {"confirm": "delete nothing"}):
        response = client.post("/local/data", json=body, headers=LOCAL_AUTH)
        assert response.status_code == 422, response.text
        assert response.json()["error"] == "invalid_request"
    with psycopg.connect(conninfo, row_factory=dict_row) as db:
        assert _total_rows(db) == seeded["pre_counts"]


def test_delete_everything(
    local_app: tuple[Any, Any, str],
    seeded: dict[str, Any],
    revoke_fakes: _Recorder,
    secret_delete_recorder: list[str],
    captured_logs: Any,
) -> None:
    """Spec 2, 5, 6: right confirm deletes every row of every public table plus
    the owner; failures at providers never block; only counts are reported."""
    client, _, conninfo = local_app
    response = client.post("/local/data", json={"confirm": "delete everything"}, headers=LOCAL_AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"deleted", "secrets", "revoked", "revoke_failed"}
    assert isinstance(body["deleted"], dict) and body["deleted"]
    assert body["deleted"].get("client", 0) >= 3
    assert body["deleted"].get("source_record", 0) >= 4
    assert body["deleted"].get("connection", 0) >= 3
    assert body["deleted"].get("job", 0) >= 2
    assert body["deleted"].get("audit_log", 0) >= 1
    for key in ("secrets", "revoked", "revoke_failed"):
        assert isinstance(body[key], int) and body[key] >= 0
    # counts report exactly the revoke attempts the fakes observed (best effort)
    assert body["revoked"] + body["revoke_failed"] == len(revoke_fakes.calls)
    if any("dead" in token for _, token in revoke_fakes.calls):
        assert body["revoke_failed"] >= 1
    # secret_delete fires once per slot the engine holds, if any were held
    assert body["secrets"] == len(secret_delete_recorder)
    with psycopg.connect(conninfo, row_factory=dict_row) as db:
        remaining = _total_rows(db)
        assert remaining == {table: 0 for table in remaining}, remaining
        assert _scalar(db, "select count(*) as n from auth.users") == 0
        assert (
            _scalar(db, "select count(*) as n from firra_local.applied_migration")
            == seeded["applied_migrations"]
        )
    text = "\n".join(captured_logs.lines)
    for needle in NEEDLES:
        assert needle not in text, f"log leak: {needle!r} in captured logs"


def test_second_call_answers_zero_counts(local_app: tuple[Any, Any, str]) -> None:
    """Spec 4: idempotent — a second call answers 200 with zero counts."""
    client, _, _ = local_app
    response = client.post("/local/data", json={"confirm": "delete everything"}, headers=LOCAL_AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert all(n == 0 for n in body["deleted"].values()), body["deleted"]
    assert body["secrets"] == 0 and body["revoked"] == 0 and body["revoke_failed"] == 0


def test_no_fixture_text_in_engine_files(local_app: tuple[Any, Any, str]) -> None:
    """Spec 3 (files): after a CHECKPOINT no file under the data directory holds
    a seeded quote, address, client name or secret (pg_wal excluded: WAL
    segments recycle asynchronously — see Report question)."""
    _, _, conninfo = local_app
    with psycopg.connect(conninfo, autocommit=True) as db:
        db.execute("checkpoint")
        row = db.execute("show data_directory").fetchone()
        assert row is not None
        data_dir = row[0]
    needles = [n.encode() for n in NEEDLES]
    hits: list[str] = []
    for root, dirs, files in os.walk(data_dir):
        if "pg_wal" in dirs:
            dirs.remove("pg_wal")
        for name in files:
            path = Path(root) / name
            try:
                blob = path.read_bytes()
            except OSError:
                continue
            if any(needle in blob for needle in needles):
                hits.append(str(path))
    assert hits == [], hits
