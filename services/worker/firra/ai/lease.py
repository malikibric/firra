"""Process-local, thirty-minute conversation leases. Database records remain pending.

A new engine needs no lease recovery: only verified submissions change processing state.
"""

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from firra.ai.packet import Packet
from firra.ai.processor import VerificationResult
from firra.db.processing import PendingRecord
from firra.db.types import WorkspaceId


class LeaseError(Exception):
    """Only an enum-like class, never packet contents or database ids."""


@dataclass
class Lease:
    id: UUID
    workspace_id: WorkspaceId
    expires_at: datetime
    packet: Packet = field(repr=False)
    records: Sequence[PendingRecord] = field(repr=False)
    result: VerificationResult | None = None


class LeaseStore:
    def __init__(self, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.clock = clock
        self._leases: dict[UUID, Lease] = {}
        self._active: dict[WorkspaceId, UUID] = {}
        # ponytail: one owner; use workspace locks if throughput grows.
        self._lock = threading.RLock()

    def get(
        self,
        workspace_id: WorkspaceId,
        factory: Callable[[], tuple[Packet, Sequence[PendingRecord]]],
        *,
        still_eligible: Callable[[Lease], bool] | None = None,
    ) -> Lease:
        with self._lock:
            self._expire_payloads()
            active_id = self._active.get(workspace_id)
            current = self._leases.get(active_id) if active_id is not None else None
            if current is not None and current.expires_at > self.clock():
                if still_eligible is None or still_eligible(current):
                    return current
                current.expires_at = self.clock()
                self._expire_payloads()
            packet, records = factory()
            if not packet.handles:
                raise LeaseError("nothing_pending")
            lease = Lease(
                uuid4(), workspace_id, self.clock() + timedelta(minutes=30), packet, tuple(records)
            )
            self._leases[lease.id] = lease
            self._active[workspace_id] = lease.id
            return lease

    def submit(
        self,
        workspace_id: WorkspaceId,
        lease_id: UUID,
        operation: Callable[[Lease], VerificationResult],
    ) -> VerificationResult:
        with self._lock:
            self._expire_payloads()
            lease = self._leases.get(lease_id)
            if lease is None or lease.workspace_id != workspace_id:
                raise LeaseError("unknown_lease")
            if lease.expires_at <= self.clock():
                raise LeaseError("lease_expired")
            if lease.result is None:
                lease.result = operation(lease)
                self._forget_packet(lease)
                if self._active.get(workspace_id) == lease.id:
                    del self._active[workspace_id]
            return lease.result

    @staticmethod
    def _forget_packet(lease: Lease) -> None:
        lease.packet = replace(lease.packet, text="", handles={})
        lease.records = ()

    def _expire_payloads(self) -> None:
        now = self.clock()
        for key, lease in list(self._leases.items()):
            if lease.expires_at <= now:
                self._forget_packet(lease)
                if self._active.get(lease.workspace_id) == key:
                    del self._active[lease.workspace_id]
                # Keep only a short tombstone for the useful lease_expired error.
                if lease.expires_at + timedelta(minutes=30) <= now:
                    del self._leases[key]
