"""Device registry and request routing, all in memory (the relay stores nothing).

A device is an Ed25519 key that proved possession on its outbound WebSocket. Connector-link grants
(sha256 of a secret) are kept only while that socket is open; the engine re-sends them at every
connect. A request is forwarded to the device with the secret in ``authorization``; the device
checks it again, so a compromised relay cannot make an engine answer for a link it never issued.
"""

import asyncio
import hashlib
import hmac
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

MAX_IN_FLIGHT = 8
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
PER_DEVICE_PER_MINUTE = 120
PER_IP_PER_MINUTE = 240
CONNECTS_PER_IP_PER_MINUTE = 30
MAX_DEVICES = 2000


class SlidingLimit:
    """At most ``limit`` events per key in any 60 seconds (in memory)."""

    def __init__(self, limit: int, clock: Any = time.monotonic) -> None:
        self.limit = limit
        self.clock = clock
        self._events: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = self.clock()
        events = self._events.setdefault(key, deque())
        while events and now - events[0] > 60:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        if len(self._events) > 10_000:  # forget idle keys so memory stays bounded
            for k in [k for k, v in self._events.items() if not v or now - v[-1] > 60]:
                self._events.pop(k, None)
        return True


@dataclass
class Pending:
    """One in-flight request: the device's frames for it arrive on ``frames``."""

    frames: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=64))

    def finish(self, frame: dict[str, Any]) -> None:
        """Queues a terminal frame even when the queue is full (the oldest frames are dropped)."""
        while self.frames.full():
            self.frames.get_nowait()
        self.frames.put_nowait(frame)


@dataclass
class Device:
    device_id: str
    send: Any  # async callable(dict) -> None
    grants: dict[str, str] = field(default_factory=dict)  # grant id -> sha256 hex of the secret
    pending: dict[str, Pending] = field(default_factory=dict)

    def has_secret(self, secret: str) -> bool:
        digest = hashlib.sha256(secret.encode()).hexdigest()
        return any(hmac.compare_digest(digest, h) for h in self.grants.values())


class Hub:
    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}
        self.device_limit = SlidingLimit(PER_DEVICE_PER_MINUTE)
        self.ip_limit = SlidingLimit(PER_IP_PER_MINUTE)
        self.connect_limit = SlidingLimit(CONNECTS_PER_IP_PER_MINUTE)

    def register(self, device: Device) -> Device | None:
        """Adds (or replaces) a device; returns the connection it replaced."""
        previous = self.devices.get(device.device_id)
        self.devices[device.device_id] = device
        return previous

    def unregister(self, device: Device) -> None:
        if self.devices.get(device.device_id) is device:
            del self.devices[device.device_id]
        for pending in device.pending.values():
            pending.finish({"type": "error", "code": "device_gone"})

    def online(self, device_id: str) -> Device | None:
        return self.devices.get(device_id)
