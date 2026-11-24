"""Unit tests for the L2-04 test-Gmail seeder (fake Gmail/Calendar, no network)."""

import importlib.util
import json
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "seed-test-gmail" / "seed_test_gmail.py"

spec = importlib.util.spec_from_file_location("seed_test_gmail", SCRIPT)
assert spec and spec.loader
seed = importlib.util.module_from_spec(spec)
sys.modules["seed_test_gmail"] = seed
spec.loader.exec_module(seed)

ACCOUNT = "firra.test.northbeam@gmail.com"


def _message(
    external_id: str, thread: str, sender: str, to: str, occurred: str, subject: str = "s"
) -> dict[str, Any]:
    return {
        "external_id": external_id,
        "thread_key": thread,
        "sender": {"email": sender, "name": "N"},
        "to": [{"email": to, "name": "M"}],
        "cc": [],
        "subject": subject,
        "occurred_at": occurred,
        "message_id_header": f"<{external_id}@synthetic.northbeam.example>",
        "body_text": "Hello world, this is a test.",
    }


def _messages() -> list[dict[str, Any]]:
    return [
        _message("m1", "t1", "ruth@sable.example", "ana@northbeam.example", "2025-10-01T17:22:00Z"),
        _message("m2", "t1", "ana@northbeam.example", "ruth@sable.example", "2025-10-02T09:00:00Z"),
        _message("m3", "t2", "bob@northbeam.example", "cid@acme.example", "2025-10-03T09:00:00Z"),
    ]


def _parse(raw: bytes) -> Any:
    return BytesParser(policy=policy.default).parsebytes(raw)


def test_thread_chain_sets_in_reply_to_and_references() -> None:
    messages = _messages()
    refs = seed.thread_refs(messages)
    assert refs["m1"] == {"in_reply_to": None, "references": None}
    assert refs["m2"]["in_reply_to"] == "<m1@synthetic.northbeam.example>"
    assert refs["m2"]["references"] == "<m1@synthetic.northbeam.example>"


def test_rfc822_keeps_original_date_headers_and_threading() -> None:
    messages = _messages()
    refs = seed.thread_refs(messages)
    parsed = _parse(seed.build_rfc822(messages[1], ACCOUNT, refs))
    assert parsed["Message-ID"] == "<m2@synthetic.northbeam.example>"
    assert parsed["In-Reply-To"] == "<m1@synthetic.northbeam.example>"
    assert parsed["Date"].datetime.isoformat() == "2025-10-02T09:00:00+00:00"


def test_firm_side_addresses_become_the_test_account() -> None:
    messages = _messages()
    refs = seed.thread_refs(messages)
    inbound = _parse(seed.build_rfc822(messages[0], ACCOUNT, refs))
    assert inbound["From"] == "N <ruth@sable.example>"
    assert ACCOUNT in inbound["To"]
    outbound = _parse(seed.build_rfc822(messages[1], ACCOUNT, refs))
    assert ACCOUNT in outbound["From"]
    assert "ruth@sable.example" in outbound["To"]


class FakeGoogle:
    """Minimal Gmail/Calendar fake behind httpx.MockTransport."""

    def __init__(self, fail_first_import: int = 0) -> None:
        self.imported: list[dict[str, Any]] = []
        self.events: dict[str, dict[str, Any]] = {}
        self.patched_cancelled: list[str] = []
        self.import_attempts = 0
        self.fail_first_import = fail_first_import

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            path, params = request.url.path, dict(request.url.params)
            if path.endswith("/userinfo"):
                return httpx.Response(200, json={"email": ACCOUNT})
            if path.endswith("/messages") and request.method == "GET":
                query = params.get("q", "")
                if query.startswith("rfc822msgid:"):
                    found = [
                        m
                        for m in self.imported
                        if m["message_id"] == query.removeprefix("rfc822msgid:")
                    ]
                    return httpx.Response(200, json={"messages": [{"id": "x"}]} if found else {})
                return httpx.Response(200, json={})
            if path.endswith("/messages/import") and request.method == "POST":
                self.import_attempts += 1
                if self.import_attempts <= self.fail_first_import:
                    return httpx.Response(429, json={"error": "slow down"})
                body = json.loads(request.content)
                raw = body["message"]["raw"]
                self.imported.append(
                    {
                        "message_id": _parse(_b64(raw))["Message-ID"],
                        "labels": body["message"]["labelIds"],
                    }
                )
                return httpx.Response(200, json={"id": "gid"})
            if path.endswith("/messages") and request.method == "POST":
                body = json.loads(request.content)
                self.imported.append(
                    {
                        "message_id": _parse(_b64(body["raw"]))["Message-ID"],
                        "labels": body.get("labelIds", []),
                    }
                )
                return httpx.Response(200, json={"id": "gid"})
            if "/events/" in path and request.method == "GET":
                event_id = path.rsplit("/", 1)[-1]
                if event_id in self.events:
                    return httpx.Response(200, json={"id": event_id})
                return httpx.Response(404, json={})
            if path.endswith("/events") and request.method == "POST":
                body = json.loads(request.content)
                self.events[body["id"]] = body
                return httpx.Response(200, json={"id": body["id"]})
            if "/events/" in path and request.method == "PATCH":
                self.patched_cancelled.append(path.rsplit("/", 1)[-1])
                return httpx.Response(200, json={})
            raise AssertionError(f"unexpected {request.method} {path}")

        return httpx.MockTransport(handler)


def _b64(raw_b64: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4))


def _client(fake: FakeGoogle) -> Any:
    http = httpx.Client(transport=fake.transport())
    client = seed.SeedClient(http, "token", pacer_interval=0.0)
    return client


def test_seed_imports_once_and_rerun_adds_nothing() -> None:
    fake = FakeGoogle()
    client = _client(fake)
    state: dict[str, Any] = {"messages": [], "events": []}
    added = seed.seed(client, _messages(), [], ACCOUNT, state, method="import")
    assert added == {"messages": 3, "skipped": 0, "events": 0, "events_skipped": 0}
    labels = {m["message_id"]: m["labels"] for m in fake.imported}
    assert labels["<m2@synthetic.northbeam.example>"] == ["SENT"]
    assert labels["<m1@synthetic.northbeam.example>"] == ["INBOX"]
    rerun = seed.seed(client, _messages(), [], ACCOUNT, state, method="import")
    assert rerun["messages"] == 0 and rerun["skipped"] == 3
    assert len(fake.imported) == 3


def test_retry_on_429_then_resume_from_state() -> None:
    fake = FakeGoogle(fail_first_import=1)
    client = _client(fake)
    real_sleep = seed.time.sleep
    seed.time.sleep = lambda seconds: None
    try:
        state: dict[str, Any] = {"messages": [], "events": []}
        added = seed.seed(client, _messages()[:1], [], ACCOUNT, state, method="import")
    finally:
        seed.time.sleep = real_sleep
    assert added["messages"] == 1
    assert state["messages"] == ["m1"]


def test_cancelled_event_is_inserted_then_cancelled() -> None:
    fake = FakeGoogle()
    client = _client(fake)
    event = {
        "external_id": "brightpath-meeting-001",
        "starts_at": "2025-10-08T15:00:00Z",
        "ends_at": "2025-10-08T15:30:00Z",
        "status": "cancelled",
        "title": "check-in",
        "attendees": [{"email": "ana@northbeam.example", "name": "Ana"}],
    }
    state: dict[str, Any] = {"messages": [], "events": []}
    added = seed.seed(client, [], [event], ACCOUNT, state, method="import")
    assert added["events"] == 1
    event_id = seed.calendar_event_id("brightpath-meeting-001")
    assert event_id in fake.events
    assert fake.patched_cancelled == [event_id]
    assert (
        fake.events[event_id]["extendedProperties"]["private"]["externalId"]
        == "brightpath-meeting-001"
    )


def test_account_allow_list_refuses_a_real_mailbox() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"email": "founder@realclient.com"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SystemExit, match="refusing"):
        seed.check_account(http, "token", account=ACCOUNT, allow_domain=None)


def test_dry_run_report_counts() -> None:
    report = seed.dry_run_report(_messages(), [{"starts_at": "2025-10-08T15:00:00Z"}])
    assert report == (
        "messages=3 threads=2 inbound=1 outbound=2 events=1 "
        "earliest=2025-10-01T17:22:00Z latest=2025-10-03T09:00:00Z"
    )
