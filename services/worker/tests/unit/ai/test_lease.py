"""Lease lifetime, single ownership, retries and duplicate submission with a fake clock."""

import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from tests.unit.ai.test_processor import record

from firra.ai.lease import Lease, LeaseError, LeaseStore
from firra.ai.packet import Packet, build_packet
from firra.ai.processor import VerificationResult
from firra.db.ai_processing import PacketRecord
from firra.db.types import WorkspaceId

State = tuple[LeaseStore, list[datetime], WorkspaceId]


@pytest.fixture
def state() -> State:
    now = [datetime(2026, 10, 5, tzinfo=UTC)]
    return LeaseStore(clock=lambda: now[0]), now, WorkspaceId(uuid4())


def packet() -> tuple[Packet, list[PacketRecord]]:
    source = record()
    return build_packet([source], {}, prompt_version="extract.v6"), [source]


def test_one_live_lease_reuses_packet_and_excludes_other_selection(state: State) -> None:
    store, _, ws = state
    first = store.get(ws, packet)
    second = store.get(ws, lambda: pytest.fail("a second selector must not reserve the same rows"))
    assert first is second
    other = store.get(WorkspaceId(uuid4()), packet)
    assert other.id != first.id and other.packet.handles != first.packet.handles
    assert first.expires_at == datetime(2026, 10, 5, 0, 30, tzinfo=UTC)
    assert (record().body_clean or "") not in repr(first)


def test_expiry_and_restart_release_pending_records(state: State) -> None:
    store, now, ws = state
    first = store.get(ws, packet)
    now[0] += timedelta(minutes=30)
    with pytest.raises(LeaseError, match="lease_expired"):
        store.submit(ws, first.id, lambda _: pytest.fail("expired lease stored"))
    assert first.records == () and first.packet.text == ""
    assert store.get(ws, packet).id != first.id
    restarted = LeaseStore(clock=lambda: now[0])
    assert restarted.get(ws, packet).id != first.id
    with pytest.raises(LeaseError, match="unknown_lease"):
        restarted.submit(ws, first.id, lambda _: pytest.fail("restart restored a lease"))


def test_unknown_and_foreign_lease_do_not_run_operation(state: State) -> None:
    store, _, ws = state
    lease = store.get(ws, packet)
    for workspace, lease_id in [(ws, uuid4()), (WorkspaceId(uuid4()), lease.id)]:
        with pytest.raises(LeaseError, match="unknown_lease"):
            store.submit(workspace, lease_id, lambda _: pytest.fail("foreign lease stored"))


def test_duplicate_submit_returns_first_counts_and_releases_text(state: State) -> None:
    store, _, ws = state
    lease = store.get(ws, packet)
    counts = VerificationResult(1, {"quote_not_found": 1}, 1, 0)
    assert store.submit(ws, lease.id, lambda _: counts) == counts
    assert store.submit(ws, lease.id, lambda _: pytest.fail("second submit stored")) == counts
    assert lease.records == () and lease.packet.text == ""
    next_lease = store.get(ws, packet)
    assert next_lease.id != lease.id
    assert store.submit(ws, lease.id, lambda _: pytest.fail("old completed lease stored")) == counts


def test_failed_submit_can_retry_without_replacing_lease(state: State) -> None:
    store, _, ws = state
    lease = store.get(ws, packet)

    def failed(_: Lease) -> VerificationResult:
        raise LeaseError("invalid_output")

    with pytest.raises(LeaseError, match="invalid_output"):
        store.submit(ws, lease.id, failed)
    assert store.get(ws, packet).id == lease.id
    assert store.submit(ws, lease.id, lambda _: VerificationResult(0, {}, 1, 0)).records_done == 1


def test_empty_selector_has_short_error(state: State) -> None:
    store, _, ws = state
    with pytest.raises(LeaseError, match="nothing_pending"):
        store.get(ws, lambda: (build_packet([], {}, prompt_version="extract.v6"), []))


def test_concurrent_submissions_execute_once(state: State) -> None:
    store, _, ws = state
    lease = store.get(ws, packet)
    calls: list[int] = []
    results: list[VerificationResult] = []

    def operation(_: Lease) -> VerificationResult:
        calls.append(1)
        return VerificationResult(1, {}, 1, 0)

    def submit() -> None:
        results.append(store.submit(ws, lease.id, operation))

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert len(calls) == 1 and len(results) == 4
