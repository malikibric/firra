"""Slack connector on bundled PostgreSQL using only synthetic MockTransport data."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from firra.connectors.slack.local_tokens import token_slot
from firra.db.types import WorkspaceId
from firra.local.profile import LocalRuntime, build_settings
from firra.local.readctx import ReadContext
from firra.local.routes.slack import build_router
from firra.local.secrets import SecretStore
from firra.local.server import create_local_app
from firra.tasks.execute import execute_envelope
from firra_contracts import EngineStatus, TaskEnvelope
from tests.fake_slack import FakeSlack
from tests.integration.conftest import LOCAL_AUTH, LOCAL_PORT, LOCAL_TOKEN

pytestmark = pytest.mark.integration
TOKEN = "xoxp-synthetic-slack-integration-token"


class Queue:
    def __init__(self) -> None:
        self.tasks: list[TaskEnvelope] = []
        self.delays: list[float] = []

    def enqueue(self, envelope: TaskEnvelope, *, delay_seconds: float = 0) -> None:
        self.tasks.append(envelope)
        self.delays.append(delay_seconds)


@pytest.fixture
def world(local_app: tuple[Any, Any, str], tmp_path: Path) -> Iterator[dict[str, Any]]:
    original, readctx, conninfo = local_app
    secrets = SecretStore()
    secrets.set("db_encryption_key", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAE=")
    runtime = LocalRuntime(build_settings(conninfo, tmp_path), secrets, identity=readctx.identity)
    fake = FakeSlack()
    queue = Queue()
    http = httpx.Client(transport=httpx.MockTransport(fake))
    tasks = replace(runtime.context, http=http, queue=queue)
    ws = WorkspaceId(readctx.identity.workspace_id)
    ctx = ReadContext(pool=runtime.pool, identity=readctx.identity)
    deleted: list[str] = []
    status = EngineStatus.model_validate(original.get("/local/status", headers=LOCAL_AUTH).json())
    app = create_local_app(
        api_token=LOCAL_TOKEN,
        port=LOCAL_PORT,
        status=lambda: status,
        routers=[
            build_router(ctx, secrets=secrets, tasks=tasks, send_secret_delete=deleted.append)
        ],
    )
    ids = {}
    with runtime.pool.connection() as conn:
        for name, policy in [
            ("full", "full"),
            ("metadata", "metadata_only"),
            ("excluded", "full"),
        ]:
            row = conn.execute(
                "insert into public.client (workspace_id,name,policy) values "
                "(%s,%s,%s) returning id",
                (ws, "Acme " + name, policy),
            ).fetchone()
            assert row is not None
            ids[name] = row["id"]
        conn.execute(
            "insert into public.client_domain (workspace_id,client_id,domain) "
            "values (%s,%s,'acme.example')",
            (ws, ids["full"]),
        )
    with TestClient(app, base_url=f"http://127.0.0.1:{LOCAL_PORT}") as api:
        try:
            yield dict(
                api=api,
                ctx=tasks,
                ws=ws,
                secrets=secrets,
                fake=fake,
                queue=queue,
                clients=ids,
                identity=readctx.identity,
                deleted=deleted,
            )
        finally:
            with runtime.pool.connection() as conn:
                conn.execute(
                    "delete from public.connection where workspace_id=%s and provider='slack'",
                    (ws,),
                )
                conn.execute("delete from public.person where workspace_id=%s", (ws,))
                conn.execute(
                    "delete from public.client where workspace_id=%s and id=any(%s)",
                    (ws, list(ids.values())),
                )
            runtime.stop()
            http.close()


def connect(world: dict[str, Any]) -> UUID:
    connection_id = uuid4()
    world["secrets"].set(token_slot(connection_id), TOKEN)
    response = world["api"].post(
        "/local/connections/slack",
        headers=LOCAL_AUTH,
        json={"connection_id": str(connection_id), "account_label": "Synthetic Workspace"},
    )
    assert response.status_code == 200, response.text
    return connection_id


def map_channel(
    world: dict[str, Any], connection_id: UUID, channel: str, client: str = "full"
) -> None:
    response = world["api"].put(
        f"/local/connections/slack/{connection_id}/scope",
        headers=LOCAL_AUTH,
        json={
            "channels": [
                {
                    "external_id": channel,
                    "label": "Synthetic Channel",
                    "client_id": str(world["clients"][client]),
                    "include": True,
                    "meta": {},
                }
            ]
        },
    )
    assert response.status_code == 200, response.text


def run(world: dict[str, Any], envelope: TaskEnvelope) -> Any:
    ctx = world["ctx"]
    return execute_envelope(
        ctx.jobs,
        ctx,
        envelope,
        requeue=lambda task, delay: world["queue"].enqueue(task, delay_seconds=delay),
    )


def drain(world: dict[str, Any]) -> None:
    steps = 0
    while world["queue"].tasks:
        envelope = world["queue"].tasks.pop(0)
        result = run(world, envelope)
        assert result.status in ("succeeded", "ignored"), result
        steps += 1
        assert steps < 100


def poll(world: dict[str, Any], connection_id: UUID) -> Any:
    key = f"slack-test:{uuid4()}"
    payload = {"connection_id": str(connection_id)}
    job_id = world["ctx"].jobs.create(
        workspace_id=world["ws"],
        job_type="sync.slack",
        idempotency_key=key,
        payload=payload,
        connection_id=connection_id,
    )
    assert job_id is not None
    return run(
        world,
        TaskEnvelope(
            workspace_id=world["ws"],
            job_id=job_id,
            type="sync.slack",
            idempotency_key=key,
            payload=payload,
        ),
    )


def rows(world: dict[str, Any]) -> list[dict[str, Any]]:
    with world["ctx"].pool.connection() as conn:
        result = conn.execute(
            "select external_id,kind::text,direction::text,body_clean,word_count,"
            "processing_state::text,content_hash,processing_attempts,author_person_id,metadata "
            "from public.source_record "
            "where workspace_id=%s order by external_id",
            (world["ws"],),
        ).fetchall()
        return list(result)


def test_backfill_pagination_minimization_and_slack_connect(
    world: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    connection_id = connect(world)
    map_channel(world, connection_id, "CMAPPED")
    map_channel(world, connection_id, "CINTERNAL")
    drain(world)
    records = rows(world)
    assert len(records) == 5 and all(row["kind"] == "chat" for row in records)
    assert all(str(row["body_clean"]).startswith("enc:") for row in records)
    assert all(row["processing_state"] == "pending" and row["word_count"] >= 5 for row in records)
    assert {row["direction"] for row in records} == {"in", "out", "internal"}
    assert not any(row["external_id"].startswith(("CUNMAPPED:", "DCOLLEAGUE:")) for row in records)
    history = [
        args["channel"] for name, args in world["fake"].calls if name == "conversations.history"
    ]
    assert "CUNMAPPED" not in history and "DCOLLEAGUE" not in history
    assert any(
        args.get("cursor") for name, args in world["fake"].calls if name == "conversations.history"
    )
    with world["ctx"].pool.connection() as conn:
        assert (
            conn.execute(
                "select count(*) as n from public.person_identity where "
                "workspace_id=%s and kind='slack_id' and value='UCOLLEAGUE'",
                (world["ws"],),
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "select count(*) as n from public.person_identity where "
                "workspace_id=%s and kind='slack_id' and value='UNOEMAIL'",
                (world["ws"],),
            ).fetchone()["n"]
            == 1
        )
    before = [(row["external_id"], row["content_hash"]) for row in records]
    assert poll(world, connection_id).status == "succeeded"
    assert [(row["external_id"], row["content_hash"]) for row in rows(world)] == before
    for sensitive in [
        TOKEN,
        "UNMAPPED_SYNTHETIC_PRIVATE_TEXT",
        "COLLEAGUE_SYNTHETIC_PRIVATE_TEXT",
        "client@acme.example",
        "Synthetic Client",
    ]:
        assert sensitive not in caplog.text


def test_unmapped_and_colleague_dm_create_no_rows(world: dict[str, Any]) -> None:
    # No client domain match for the external DM; no channel is mapped.
    with world["ctx"].pool.connection() as conn:
        conn.execute("delete from public.client_domain where workspace_id=%s", (world["ws"],))
    connect(world)
    drain(world)
    assert rows(world) == []
    with world["ctx"].pool.connection() as conn:
        for query in (
            "select count(*) as n from public.person where workspace_id=%s",
            "select count(*) as n from public.person_identity where workspace_id=%s",
            "select count(*) as n from public.evidence where workspace_id=%s",
            "select count(*) as n from public.extraction where workspace_id=%s",
        ):
            assert (
                conn.execute(
                    query,
                    (world["ws"],),
                ).fetchone()["n"]
                == 0
            )
    assert not any(
        name in ("conversations.history", "conversations.replies")
        for name, _ in world["fake"].calls
    )


def test_metadata_only_and_excluded_channels(world: dict[str, Any]) -> None:
    # Suppress the independent permitted client DM to isolate the policy cases.
    world["fake"].channels.pop("DEXTERNAL")
    connection_id = connect(world)
    map_channel(world, connection_id, "CMAPPED", "metadata")
    map_channel(world, connection_id, "CINTERNAL", "excluded")
    with world["ctx"].pool.connection() as conn, conn.transaction():
        conn.execute(
            "select set_config('request.jwt.claim.sub',%s,true)", (str(world["identity"].user_id),)
        )
        conn.execute(
            "select public.set_client_policy(%s,'excluded')", (world["clients"]["excluded"],)
        )
    drain(world)
    records = rows(world)
    assert len(records) == 3
    assert all(row["body_clean"] is None and row["content_hash"] is None for row in records)
    assert all(row["processing_state"] == "not_applicable" for row in records)
    assert not any(
        name == "conversations.history" and args["channel"] == "CINTERNAL"
        for name, args in world["fake"].calls
    )


def test_throttle_revocation_wrong_scopes_and_disconnect(world: dict[str, Any]) -> None:
    connection_id = connect(world)
    map_channel(world, connection_id, "CMAPPED")
    world["fake"].throttle = "conversations.history"
    envelope = world["queue"].tasks.pop(0)
    result = run(world, envelope)
    assert result.status == "continued" and world["queue"].delays[-1] == 120
    drain(world)
    world["fake"].error = "token_revoked"
    assert poll(world, connection_id).status == "failed"
    with world["ctx"].pool.connection() as conn:
        row = conn.execute(
            "select status::text,token_ciphertext from public.connection "
            "where workspace_id=%s and id=%s",
            (world["ws"], connection_id),
        ).fetchone()
        assert row["status"] == "revoked" and row["token_ciphertext"] is None
    world["fake"].error = None
    response = world["api"].delete(f"/local/connections/slack/{connection_id}", headers=LOCAL_AUTH)
    assert response.status_code == 204
    assert world["secrets"].get(token_slot(connection_id)) is None
    assert world["deleted"] == [token_slot(connection_id)]
    assert any(name == "auth.revoke" for name, _ in world["fake"].calls)
    world["fake"].scopes.append("chat:write")
    another = uuid4()
    world["secrets"].set(token_slot(another), TOKEN)
    response = world["api"].post(
        "/local/connections/slack",
        headers=LOCAL_AUTH,
        json={"connection_id": str(another), "account_label": "Synthetic Wrong Scope"},
    )
    assert response.status_code == 422 and response.json()["error"] == "scopes_too_broad"


def test_old_thread_reply_first_recent_reply_edit_and_delete(world: dict[str, Any]) -> None:
    fake = world["fake"]
    parent = fake.add(
        "CMAPPED",
        "Please review the original proposal before next Monday.",
        days=30,
        user="UCLIENT",
    )
    parent.update(thread_ts=parent["ts"], reply_count=1)
    old = {
        "type": "message",
        "user": "UOWNER",
        "text": "We will review that proposal tomorrow morning.",
        "ts": f"{(fake.now - timedelta(days=29)).timestamp():.6f}",
        "thread_ts": parent["ts"],
    }
    parent["latest_reply"] = old["ts"]
    fake.threads[("CMAPPED", parent["ts"])] = [parent, old]
    connection_id = connect(world)
    map_channel(world, connection_id, "CMAPPED")
    drain(world)
    reply = {
        "type": "message",
        "user": "UCLIENT",
        "text": "Please include the revised scope in the proposal.",
        "ts": f"{(fake.now - timedelta(hours=12)).timestamp():.6f}",
        "thread_ts": parent["ts"],
    }
    parent.update(latest_reply=reply["ts"], reply_count=2)
    fake.threads[("CMAPPED", parent["ts"])].append(reply)
    recent = fake.messages["CMAPPED"][0]
    first = {
        "type": "message",
        "user": "UOWNER",
        "text": "We will provide the requested report before Friday.",
        "ts": f"{(fake.now - timedelta(hours=6)).timestamp():.6f}",
        "thread_ts": recent["ts"],
    }
    recent.update(thread_ts=recent["ts"], reply_count=1, latest_reply=first["ts"])
    fake.threads[("CMAPPED", recent["ts"])] = [recent, first]
    assert poll(world, connection_id).status == "succeeded"
    ids = {row["external_id"] for row in rows(world)}
    assert f"CMAPPED:{reply['ts']}" in ids and f"CMAPPED:{first['ts']}" in ids

    edited = fake.messages["CMAPPED"][1]
    edited_id = f"CMAPPED:{edited['ts']}"
    with world["ctx"].pool.connection() as conn:
        conn.execute(
            "update public.source_record set processing_state='done',processing_attempts=2 "
            "where workspace_id=%s and external_id=%s",
            (world["ws"], edited_id),
        )
    assert poll(world, connection_id).status == "succeeded"
    unchanged = next(row for row in rows(world) if row["external_id"] == edited_id)
    assert unchanged["processing_state"] == "done" and unchanged["processing_attempts"] == 2
    edited["text"] += " We will also attach the updated estimates."
    edited["edited"] = {"user": "UOWNER", "ts": f"{fake.now.timestamp():.6f}"}
    assert poll(world, connection_id).status == "succeeded"
    changed = next(row for row in rows(world) if row["external_id"] == edited_id)
    assert changed["content_hash"] != unchanged["content_hash"]
    assert changed["processing_state"] == "pending" and changed["processing_attempts"] == 0

    missing = fake.messages["CMAPPED"][2]
    missing_id = f"CMAPPED:{missing['ts']}"
    with world["ctx"].pool.connection() as conn, conn.transaction():
        record = conn.execute(
            "select id,client_id from public.source_record "
            "where workspace_id=%s and external_id=%s",
            (world["ws"], missing_id),
        ).fetchone()
        assert record is not None
        evidence = conn.execute(
            "insert into public.evidence (workspace_id,source_record_id,client_id,quote,locator) "
            "values (%s,%s,%s,'We need more evidence before the renewal conversation.','{}') "
            "returning id",
            (world["ws"], record["id"], record["client_id"]),
        ).fetchone()
        extraction = conn.execute(
            "insert into public.extraction (workspace_id,source_record_id,client_id,kind,payload,"
            "model,prompt_version,processing_version,item_index) "
            "values (%s,%s,%s,'request','{}','synthetic','test',0,0) returning id",
            (world["ws"], record["id"], record["client_id"]),
        ).fetchone()
        assert evidence is not None and extraction is not None
        conn.execute(
            "insert into public.extraction_evidence "
            "(workspace_id,extraction_id,evidence_id,client_id) "
            "values (%s,%s,%s,%s)",
            (world["ws"], extraction["id"], evidence["id"], record["client_id"]),
        )
    fake.messages["CMAPPED"].remove(missing)
    connection = world["ctx"].connections.get(workspace_id=world["ws"], connection_id=connection_id)
    assert connection is not None
    state = dict(connection.cursor["slack_channels"]["CMAPPED"])
    state.pop("rescanned_at", None)
    from firra.db.local_slack import SlackRepo

    SlackRepo(world["ctx"].pool).save_channel(
        workspace_id=world["ws"], connection_id=connection_id, channel="CMAPPED", state=state
    )
    assert poll(world, connection_id).status == "succeeded"
    ids = {row["external_id"] for row in rows(world)}
    assert missing_id not in ids and f"CMAPPED:{reply['ts']}" in ids
    with world["ctx"].pool.connection() as conn:
        assert (
            conn.execute(
                "select id from public.evidence where workspace_id=%s and id=%s",
                (world["ws"], evidence["id"]),
            ).fetchone()
            is None
        )
        assert (
            conn.execute(
                "select id from public.extraction where workspace_id=%s and id=%s",
                (world["ws"], extraction["id"]),
            ).fetchone()
            is None
        )


def test_suggestions_read_no_messages_and_email_identity_is_reused(world: dict[str, Any]) -> None:
    connection_id = connect(world)
    response = world["api"].get(
        f"/local/connections/slack/{connection_id}/channels", headers=LOCAL_AUTH
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert any(item["suggested_client_id"] == str(world["clients"]["full"]) for item in items)
    assert not any(
        method in ("conversations.history", "conversations.replies")
        for method, _ in world["fake"].calls
    )
    with world["ctx"].pool.connection() as conn:
        assert (
            conn.execute(
                "select count(*) as n from public.person where workspace_id=%s", (world["ws"],)
            ).fetchone()["n"]
            == 0
        )
        person = conn.execute(
            "insert into public.person (workspace_id,client_id,side,display_name) "
            "values (%s,%s,'client','Existing Contact') returning id",
            (world["ws"], world["clients"]["full"]),
        ).fetchone()
        assert person is not None
        conn.execute(
            "insert into public.person_identity (workspace_id,person_id,kind,value) "
            "values (%s,%s,'email','client@acme.example')",
            (world["ws"], person["id"]),
        )
    map_channel(world, connection_id, "CMAPPED")
    drain(world)
    with world["ctx"].pool.connection() as conn:
        identity = conn.execute(
            "select person_id from public.person_identity where workspace_id=%s "
            "and kind='slack_id' and value='UCLIENT'",
            (world["ws"],),
        ).fetchone()
        assert identity is not None and identity["person_id"] == person["id"]
        connection = world["ctx"].connections.get(
            workspace_id=world["ws"], connection_id=connection_id
        )
        assert connection is not None and connection.external_account_id == "UOWNER"
    assert all(row["metadata"].get("channel_id") for row in rows(world))


def test_page_resume_uses_persisted_progress_and_missing_scope_stops(world: dict[str, Any]) -> None:
    connection_id = connect(world)
    map_channel(world, connection_id, "CMAPPED")
    world["fake"].throttle_second_page = True
    envelope = world["queue"].tasks.pop(0)
    assert run(world, envelope).status == "continued"
    assert world["queue"].delays[-1] == 120
    with world["ctx"].pool.connection() as conn:
        job = conn.execute(
            "select progress from public.job where workspace_id=%s and id=%s",
            (world["ws"], envelope.job_id),
        ).fetchone()
        assert job is not None and job["progress"]["page_token"] == "2"
        assert TOKEN not in str(job["progress"])
        assert not any(
            message["text"] in str(job["progress"]) for message in world["fake"].messages["CMAPPED"]
        )
    # Recreate the token provider: resume has no dependency on in-memory provider state.
    from firra.connectors.slack.local_tokens import LocalSlackTokens

    world["ctx"] = replace(world["ctx"], slack=LocalSlackTokens(world["secrets"]))
    drain(world)
    assert len(rows(world)) == 4
    world["fake"].error = "missing_scope"
    assert poll(world, connection_id).status == "failed"
    connection = world["ctx"].connections.get(workspace_id=world["ws"], connection_id=connection_id)
    assert connection is not None and connection.status == "error"
    with world["ctx"].pool.connection() as conn:
        row = conn.execute(
            "select last_error_class from public.connection where workspace_id=%s and id=%s",
            (world["ws"], connection_id),
        ).fetchone()
        assert row is not None and row["last_error_class"] == "missing_scope"


def synthetic_export(world: dict[str, Any], path: Path) -> Path:
    import json
    from zipfile import ZipFile

    fake = world["fake"]
    users = [*fake.users.values(), {"id": "UBOT", "team_id": "TOWN", "is_bot": True}]
    public = [c for c in fake.channels.values() if not c.get("is_im")]
    dms = [
        {"id": c["id"], "members": ["UOWNER", c["user"]]}
        for c in fake.channels.values()
        if c.get("is_im")
    ]
    with ZipFile(path, "w") as archive:
        for name, value in (("users.json", users), ("channels.json", public), ("dms.json", dms)):
            archive.writestr(name, json.dumps(value))
        for channel in fake.channels.values():
            messages = list(fake.messages[channel["id"]])
            if channel["id"] == "CMAPPED":
                messages += [
                    {
                        "type": "message",
                        "user": "UBOT",
                        "text": "SYNTHETIC BOT BODY",
                        "ts": "1791158400.000011",
                    },
                    {
                        "type": "message",
                        "subtype": "message_changed",
                        "user": "UCLIENT",
                        "text": "SYNTHETIC EDIT EVENT",
                        "ts": "1791158400.000012",
                    },
                    {
                        "type": "message",
                        "user": "UCLIENT",
                        "text": "SYNTHETIC BAD TS",
                        "ts": "invalid",
                    },
                    {
                        "type": "message",
                        "user": "UCLIENT",
                        "text": "Synthetic reply for the proposal.",
                        "ts": "1791158400.000014",
                        "thread_ts": "1791158400.000013",
                    },
                ]
            folder = channel.get("name") or channel["id"]
            archive.writestr(f"{folder}/2026-10-05.json", json.dumps(messages))
    return path


def test_export_shared_ingest_encryption_threads_duplicates_and_log_hygiene(
    world: dict[str, Any], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from firra.connectors.slack.export import read_export
    from firra.connectors.slack.importer import import_messages
    from firra.local.bodycrypt import read_body

    connection_id = connect(world)
    map_channel(world, connection_id, "CMAPPED")
    path = synthetic_export(world, tmp_path / "private-export-path.zip")
    response = world["api"].post(
        "/local/slack/import", headers=LOCAL_AUTH, json={"path": str(path)}
    )
    assert response.status_code == 200, response.text
    from firra_contracts.generated.local.slack_import_response import SlackImportResponse

    result = SlackImportResponse.model_validate(response.json())
    assert result.channels == 2 and result.messages_imported == 5
    records = rows(world)
    assert len(records) == 5
    assert all(str(row["body_clean"]).startswith("enc:") for row in records)
    assert all(row["processing_state"] == "pending" for row in records)
    assert not any(row["external_id"].startswith(("CUNMAPPED:", "DCOLLEAGUE:")) for row in records)
    with world["ctx"].pool.connection() as conn:
        reply = conn.execute(
            "select id,thread_key,body_clean from public.source_record "
            "where workspace_id=%s and external_id='CMAPPED:1791158400.000014'",
            (world["ws"],),
        ).fetchone()
        assert reply is not None and reply["thread_key"] == "1791158400.000013"
        assert read_body(world["ctx"].pool, world["ws"], reply["id"], reply["body_clean"]) == (
            "Synthetic reply for the proposal."
        )
        ids = conn.execute(
            "select id from public.source_record where workspace_id=%s order by id",
            (world["ws"],),
        ).fetchall()
    with read_export(path) as data:
        repeat = import_messages(
            data, tasks=world["ctx"], workspace_id=world["ws"], connection_id=connection_id
        )
    assert repeat.messages_imported == 0 and repeat.messages_dropped == result.messages_dropped
    # API history contains the imported reply too: authoritative rescans preserve the same row.
    world["fake"].messages["CMAPPED"].append(
        {
            "type": "message",
            "user": "UCLIENT",
            "text": "Synthetic reply for the proposal.",
            "ts": "1791158400.000014",
            "thread_ts": "1791158400.000013",
        }
    )
    drain(world)
    with world["ctx"].pool.connection() as conn:
        after = conn.execute(
            "select id from public.source_record where workspace_id=%s and id=any(%s) order by id",
            (world["ws"], [r["id"] for r in ids]),
        ).fetchall()
        assert after == ids
    for private in (
        "private-unmapped",
        "private-export-path",
        "UNMAPPED_SYNTHETIC_PRIVATE_TEXT",
        "SYNTHETIC BOT BODY",
        "client@acme.example",
    ):
        logged = caplog.text + str([getattr(r, "fields", {}) for r in caplog.records])
        assert private not in logged


def test_export_metadata_only_and_excluded_use_same_minimization(
    world: dict[str, Any], tmp_path: Path
) -> None:
    from firra.connectors.slack.export import read_export
    from firra.connectors.slack.importer import import_messages

    world["fake"].channels.pop("DEXTERNAL")
    connection_id = connect(world)
    map_channel(world, connection_id, "CMAPPED", "metadata")
    map_channel(world, connection_id, "CINTERNAL", "excluded")
    with world["ctx"].pool.connection() as conn, conn.transaction():
        conn.execute(
            "select set_config('request.jwt.claim.sub',%s,true)", (str(world["identity"].user_id),)
        )
        conn.execute(
            "select public.set_client_policy(%s,'excluded')", (world["clients"]["excluded"],)
        )
    with read_export(synthetic_export(world, tmp_path / "export.zip")) as data:
        result = import_messages(
            data, tasks=world["ctx"], workspace_id=world["ws"], connection_id=connection_id
        )
    assert result.channels == 1 and result.messages_imported == 4
    records = rows(world)
    assert all(row["body_clean"] is None and row["content_hash"] is None for row in records)
    assert all(row["processing_state"] == "not_applicable" for row in records)
    assert all(row["metadata"] == {"channel_id": "CMAPPED"} for row in records)


def test_export_route_errors_do_not_echo_path_or_text(
    world: dict[str, Any], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "SYNTHETIC_PRIVATE_PATH.zip"
    path.write_text("SYNTHETIC_PRIVATE_TEXT")
    for target in (str(path), "relative.zip", str(tmp_path)):
        response = world["api"].post(
            "/local/slack/import", headers=LOCAL_AUTH, json={"path": target}
        )
        assert response.status_code == 422
        assert "SYNTHETIC_PRIVATE" not in response.text
        assert str(tmp_path) not in response.text
    assert "SYNTHETIC_PRIVATE" not in caplog.text
    assert rows(world) == []


def test_export_fallback_creates_one_upload_connection_without_tokens(
    world: dict[str, Any], tmp_path: Path
) -> None:
    path = synthetic_export(world, tmp_path / "export.zip")
    response = world["api"].post(
        "/local/slack/import", headers=LOCAL_AUTH, json={"path": str(path)}
    )
    assert response.status_code == 200, response.text
    assert response.json()["messages_imported"] == 1  # only the mapped-client DM
    with world["ctx"].pool.connection() as conn:
        connections = conn.execute(
            "select id,mode::text,token_ciphertext,external_account_id,cursor "
            "from public.connection where workspace_id=%s and provider='slack'",
            (world["ws"],),
        ).fetchall()
    assert len(connections) == 1
    connection = connections[0]
    assert connection["mode"] == "upload" and connection["token_ciphertext"] is None
    assert connection["external_account_id"] == "TOWN"
    assert connection["cursor"] == {"team_id": "TOWN"}
    map_channel(world, connection["id"], "CMAPPED")
    assert world["queue"].tasks == []  # upload scopes do not enqueue token-based API sync
    response = world["api"].post(
        "/local/slack/import", headers=LOCAL_AUTH, json={"path": str(path)}
    )
    assert response.status_code == 200 and response.json()["messages_imported"] == 4
    response = world["api"].post(
        "/local/slack/import", headers=LOCAL_AUTH, json={"path": str(path)}
    )
    assert response.status_code == 200 and response.json()["messages_imported"] == 0
    assert len(rows(world)) == 5
    with world["ctx"].pool.connection() as conn:
        assert (
            conn.execute(
                "select count(*) as n from public.connection "
                "where workspace_id=%s and provider='slack'",
                (world["ws"],),
            ).fetchone()["n"]
            == 1
        )
