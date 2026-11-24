"""Independent oracle for in-conversation processing (L3-04o).

Written from the original task spec only, before the
L3-04 implementation lands, at the protocol level (JSON-RPC over POST /mcp).
The tools exist on main as "not available" stubs, so a runtime probe skips the
suite until they are implemented: the fixture calls get_pending_mail on the
empty workspace and skips while the stub answers. Every failing case below must
fail only for the reason its name states.
"""
# ruff: noqa: E501  (long bodies and SQL fixtures)

import base64
import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.db.types import WorkspaceId
from tests.integration.conftest import LOCAL_AUTH
from tests.integration.mcp_client import McpClient, is_error_result
from tests.integration.mcp_client import rpc_result as _rpc_result

MCP_PROCESSING_TOOLS = True
pytestmark = pytest.mark.integration

Processing = tuple[McpClient, Any, str, WorkspaceId, dict[str, UUID]]

STUB_TEXT = "not available"

BODIES = {
    "ca": "Please send the Q3 attribution report by Wednesday morning. The board keeps asking for pipeline numbers.",
    "cb": "The dashboard export is wrong again and totals do not match our ledger.",
    "cc": "We need SOC2 evidence from the March audit before the renewal conversation starts.",
    "cd": "Priya joined as our new head of operations last Monday morning.",
}
QUOTES = {
    "ca": "send the Q3 attribution report by Wednesday morning",
    "cb": "totals do not match our ledger",
    "cc": "need SOC2 evidence from the March audit",
    "cd": "joined as our new head of operations",
}
BODY_X = "Cut our scope next quarter because budgets are tight."


@pytest.fixture
def processing(
    local_app: tuple[Any, Any, str],
) -> Iterator[Processing]:
    """Probe first (empty workspace), then seed. Skips while L3-04 is a stub."""
    http, ctx, conninfo = local_app
    mcp = McpClient(http, LOCAL_AUTH)
    probe = mcp.tools_call("get_pending_mail", {})
    if is_error_result(probe) and STUB_TEXT in _dump(probe).lower():
        pytest.skip("L3-04 not implemented (get_pending_mail is a stub)")
    ws = WorkspaceId(ctx.identity.workspace_id)
    from firra.db.records import RecordInput, RecordRepo
    from firra.local.bodycrypt import BodyKeyProvider, bind
    from firra.local.secrets import SecretStore

    store = SecretStore()
    store.set("db_encryption_key", base64.b64encode(os.urandom(32)).decode())
    pool = ctx.pool
    bind(pool, BodyKeyProvider(store))
    ids = {name: uuid4() for name in ("full", "excluded")}
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        db.execute(
            "insert into public.client (id, workspace_id, name, policy) values "
            "(%s,%s,'Oracle Conv Full','full'),(%s,%s,'Oracle Conv Excluded','excluded')",
            (ids["full"], ws, ids["excluded"], ws),
        )
        connection = db.execute(
            "insert into public.connection (workspace_id, provider, mode, account_label)"
            " values (%s,'email_upload','upload','oracle-conv') returning id",
            (ws,),
        ).fetchone()
        assert connection is not None
        person = db.execute(
            "insert into public.person (workspace_id, client_id, side, display_name)"
            " values (%s,%s,'client','Oracle Conv Author') returning id",
            (ws, ids["full"]),
        ).fetchone()
        assert person is not None
    repo = RecordRepo(pool)
    seeds = {
        **{k: (ids["full"], body) for k, body in BODIES.items()},
        "cx": (ids["excluded"], BODY_X),
    }
    for external, (client_id, body) in seeds.items():
        repo.upsert_record(
            workspace_id=ws,
            record=RecordInput(
                connection_id=connection["id"],
                provider="email_upload",
                external_id=f"oracle-conv-{external}",
                kind="email",
                client_id=client_id,
                policy="full",
                thread_key="oracle-conv",
                occurred_at=datetime(2026, 10, 5, 9, tzinfo=UTC),
                direction="in",
                word_count=len(body.split()),
                metadata={},
                body_clean=body,
                raw_object_uri=None,
                deep_link=None,
                content_hash=b"oracle-conv",
            ),
            author_person_id=person["id"],
            participants=[],
            event=None,
        )
    try:
        yield mcp, ctx, conninfo, ws, ids
    finally:
        with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
            db.execute(
                "delete from public.connection where workspace_id=%s and id=%s",
                (ws, connection["id"]),
            )
            db.execute(
                "delete from public.person where workspace_id=%s and id=%s", (ws, person["id"])
            )
            db.execute(
                "delete from public.client where workspace_id=%s and id=any(%s)",
                (ws, list(ids.values())),
            )


def _dump(value: Any) -> str:
    return json.dumps(value, default=str)


def rpc_result(envelope: Any) -> Any:
    """Unwrap the SDK's standard tool result without changing protocol assertions."""
    result = _rpc_result(envelope)
    return result.get("structuredContent", result)


def _packet_records(packet: Any) -> list[dict[str, Any]]:
    if isinstance(packet, str):
        return [
            {"record": handle, "text": text}
            for handle, text in re.findall(
                r'<source_record id="(r[0-9]+)"[^>]*>\n(.*?)\n</source_record>',
                packet,
                re.DOTALL,
            )
        ]
    return packet if isinstance(packet, list) else packet.get("records", [])


def _lease(mcp: McpClient, args: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = rpc_result(mcp.tools_call("get_pending_mail", args or {}))
    return result


def _handle_for(packet: Any, needle: str) -> str:
    """The packet handle whose record text contains the needle."""
    records = _packet_records(packet)
    for record in records:
        if needle in _dump(record):
            handle = record.get("record") if isinstance(record, dict) else None
            assert isinstance(handle, str), f"record without a handle: {record!r:.120}"
            return handle
    raise AssertionError(f"no packet record contains {needle!r}")


def _speaker_for(packet: Any, handle: str) -> str:
    """The sender address from the packet block, or unknown when absent."""
    records = _packet_records(packet)
    for record in records:
        if isinstance(record, dict) and record.get("record") == handle:
            match = re.search(r"From:\s*(\S+@\S+)", _dump(record))
            if match:
                return match.group(1)
    return "unknown"


def _item(
    record: str, quote: str, kind: str = "request", speaker: str = "unknown"
) -> dict[str, Any]:
    return {
        "record": record,
        "items": [
            {
                "kind": kind,
                "quote": quote,
                "speaker": speaker,
                "text": "Oracle test item.",
                "subtype": None,
                "tone": None,
                "owner_side": None,
                "due_date": None,
                "metric": None,
                "change": None,
                "person_name": None,
                "role_title": None,
                "budget_direction": None,
                "initiative_type": None,
                "topic_key": "oracle-slug",
            }
        ],
    }


def _submit(mcp: McpClient, lease_id: str, results: list[dict[str, Any]]) -> Any:
    return mcp.tools_call(
        "submit_extractions", {"lease_id": lease_id, "output": {"results": results}}
    )


def _stored_count(conninfo: str, ws: WorkspaceId, quote: str) -> int:
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        row = db.execute(
            "select count(*) from public.extraction e join public.source_record r on r.id = e.source_record_id"
            " join public.extraction_evidence ee on ee.workspace_id = e.workspace_id and ee.extraction_id = e.id join public.evidence ev on ev.workspace_id = ee.workspace_id and ev.id = ee.evidence_id where r.workspace_id = %s and ev.quote = %s",
            (ws, quote),
        ).fetchone()
        assert row is not None
        return int(row["count"])


# Req 1: packet shape, 30-minute window, excluded records never inside.
def test_lease_packet_shape_and_window(
    processing: Processing,
) -> None:
    mcp, _, _, _, ids = processing
    result = _lease(mcp)
    assert set(result) >= {"lease_id", "expires_at", "packet", "instructions", "output_schema"}
    assert result["packet"], "expected pending records in a fresh lease"
    assert result["instructions"] and result["output_schema"]
    expires = datetime.fromisoformat(str(result["expires_at"]).replace("Z", "+00:00"))
    delta = (expires - datetime.now(UTC)).total_seconds() / 60
    assert 25 <= delta <= 35, f"lease window is not ~30 minutes: {delta}"
    assert BODY_X not in _dump(result) and str(ids["excluded"]) not in _dump(result)


# Req 2: a verbatim quote is accepted and stored; the record is done.
def test_valid_item_accepted_and_stored(
    processing: Processing,
) -> None:
    mcp, _, conninfo, ws, _ = processing
    result = _lease(mcp)
    handle = _handle_for(result["packet"], "attribution report")
    speaker = _speaker_for(result["packet"], handle)
    body = rpc_result(
        _submit(mcp, result["lease_id"], [_item(handle, QUOTES["ca"], speaker=speaker)])
    )
    assert body["accepted"] >= 1
    assert set(body) >= {"accepted", "rejected_by_reason", "records_done", "records_left"}
    assert BODIES["ca"] not in _dump(body), "submit response echoes record text"
    assert _stored_count(conninfo, ws, QUOTES["ca"]) == 1


# Req 2: an invented quote is counted and stored nowhere.
def test_invented_quote_rejected_and_stored_nowhere(
    processing: Processing,
) -> None:
    mcp, _, conninfo, ws, _ = processing
    result = _lease(mcp)
    invented = "the moon is made of green cheese tonight"
    handle = _handle_for(result["packet"], "dashboard export")
    speaker = _speaker_for(result["packet"], handle)
    body = rpc_result(_submit(mcp, result["lease_id"], [_item(handle, invented, speaker=speaker)]))
    assert body["accepted"] == 0
    assert body["rejected_by_reason"], "expected a rejection reason for the invented quote"
    assert invented not in _dump(body)
    assert _stored_count(conninfo, ws, invented) == 0


# Req 2: handles outside the lease are rejected, never resolved across leases.
def test_unknown_handle_rejected(
    processing: Processing,
) -> None:
    mcp, _, _, _, _ = processing
    result = _lease(mcp)
    body = rpc_result(_submit(mcp, result["lease_id"], [_item("r999", QUOTES["cb"])]))
    assert body["accepted"] == 0
    assert body["rejected_by_reason"], "expected a rejection reason for the foreign handle"


# Req 3: a policy change after the lease is issued rejects the submit.
# Uses an expendable client: excluding deletes the client's rows.
def test_policy_changed_mid_lease_rejected(
    processing: Processing,
) -> None:
    mcp, ctx, conninfo, ws, ids = processing
    from firra.db.records import RecordInput, RecordRepo

    repo = RecordRepo(ctx.pool)
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        pawn = db.execute(
            "insert into public.client (workspace_id, name, policy) values (%s,'Oracle Conv Pawn','full')"
            " returning id",
            (ws,),
        ).fetchone()
        assert pawn is not None
        ids["pawn"] = pawn["id"]
        connection = db.execute(
            "select id from public.connection where workspace_id = %s and account_label = 'oracle-conv'",
            (ws,),
        ).fetchone()
        person = db.execute(
            "select id from public.person where workspace_id = %s and display_name = 'Oracle Conv Author'",
            (ws,),
        ).fetchone()
        assert connection is not None and person is not None
        pawn_body = "The pawn stands ready for the policy change drill today."
        pawn_quote = "pawn stands ready for the policy change"
        repo.upsert_record(
            workspace_id=ws,
            record=RecordInput(
                connection_id=connection["id"],
                provider="email_upload",
                external_id="oracle-conv-pawn",
                kind="email",
                client_id=pawn["id"],
                policy="full",
                thread_key="oracle-conv",
                occurred_at=datetime(2026, 10, 5, 9, tzinfo=UTC),
                direction="in",
                word_count=len(pawn_body.split()),
                metadata={},
                body_clean=pawn_body,
                raw_object_uri=None,
                deep_link=None,
                content_hash=b"oracle-conv",
            ),
            author_person_id=person["id"],
            participants=[],
            event=None,
        )
    result = _lease(mcp)
    handle = _handle_for(result["packet"], "policy change drill")
    speaker = _speaker_for(result["packet"], handle)
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db, db.transaction():
        db.execute(
            "select set_config('request.jwt.claim.sub', %s, true)", (str(ctx.identity.user_id),)
        )
        db.execute("select public.set_client_policy(%s, 'excluded')", (pawn["id"],))
    try:
        body = rpc_result(
            _submit(mcp, result["lease_id"], [_item(handle, pawn_quote, speaker=speaker)])
        )
        assert body["accepted"] == 0
        assert body["rejected_by_reason"], "expected rejection after the mid-lease policy change"
        assert _stored_count(conninfo, ws, pawn_quote) == 0
    finally:
        with (
            psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db,
            db.transaction(),
        ):
            db.execute(
                "select set_config('request.jwt.claim.sub', %s, true)", (str(ctx.identity.user_id),)
            )
            db.execute("select public.set_client_policy(%s, 'full')", (pawn["id"],))


# Req 2: the second submit for a lease is refused and repeats the first counts.
def test_second_submit_repeats_first_counts(
    processing: Processing,
) -> None:
    mcp, _, conninfo, ws, _ = processing
    result = _lease(mcp)
    handle = _handle_for(result["packet"], "new head of operations")
    speaker = _speaker_for(result["packet"], handle)
    first = rpc_result(
        _submit(mcp, result["lease_id"], [_item(handle, QUOTES["cd"], speaker=speaker)])
    )
    assert first["accepted"] >= 1
    second = rpc_result(
        _submit(mcp, result["lease_id"], [_item(handle, QUOTES["cd"], speaker=speaker)])
    )
    assert second == first
    assert _stored_count(conninfo, ws, QUOTES["cd"]) == 1, "second submit stored a duplicate"


# Req 5: error catalogue — unknown lease, invalid output, nothing pending.
def test_unknown_lease_is_an_error(
    processing: Processing,
) -> None:
    mcp, _, _, _, _ = processing
    envelope = _submit(mcp, str(uuid4()), [_item("r1", QUOTES["ca"])])
    assert is_error_result(envelope) or "error" in envelope["body"]
    assert "r1" not in _dump(envelope) and QUOTES["ca"] not in _dump(envelope)


def test_invalid_output_is_an_error(
    processing: Processing,
) -> None:
    mcp, _, _, _, _ = processing
    result = _lease(mcp)
    envelope = _submit(mcp, result["lease_id"], [{"record": "r1"}])
    assert is_error_result(envelope) or "error" in envelope["body"]


def test_nothing_pending_after_drain(
    processing: Processing,
) -> None:
    mcp, _, _, _, _ = processing
    for _ in range(10):
        lease = mcp.tools_call("get_pending_mail", {})
        if is_error_result(lease) or "error" in lease["body"]:
            assert "pending" in _dump(lease).lower()
            return
        result = rpc_result(lease)
        results = []
        records = _packet_records(result["packet"])
        for record in records:
            text = record.get("text", "") if isinstance(record, dict) else ""
            words = re.findall(r"[A-Za-z']+", text)
            handle = record.get("record", "r1") if isinstance(record, dict) else "r1"
            if len(words) >= 5:
                results.append(
                    _item(handle, " ".join(words[:5]), speaker=_speaker_for(records, handle))
                )
        rpc_result(_submit(mcp, result["lease_id"], results))
    raise AssertionError("queue did not drain in 10 leases")


# Req 5: one audit row per call.
def test_each_call_writes_one_audit_row(
    processing: Processing,
) -> None:
    mcp, _, conninfo, _, _ = processing
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        before = db.execute(
            "select count(*) from public.audit_log where actor_type = 'mcp'"
        ).fetchone()
        assert before is not None
    rpc_result(mcp.tools_call("get_pending_mail", {}))
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        after = db.execute(
            "select count(*) from public.audit_log where actor_type = 'mcp'"
        ).fetchone()
        assert after is not None
        assert int(after["count"]) == int(before["count"]) + 1


def test_restart_clears_leases() -> None:
    pytest.skip(
        "needs an engine restart harness; leases are specified in-memory so a restart must drop them"
    )
