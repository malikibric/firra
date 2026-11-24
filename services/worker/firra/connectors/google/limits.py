"""Google throttling: Gmail and Calendar answer per-user rate and concurrency limits with a 403.

Gmail's binding limit is ``totalQueryCostPerMinutePerUser`` = 6000 (Cloud Console quotas and
``serviceruntime.googleapis.com/quota/limit`` for the project); a ``format=raw`` messages.get
cost about 12 units in staging. Every Gmail client paces itself to ``GMAIL_REQUEST_INTERVAL``:
two paced month chunks plus incremental sync stay near half of the limit.

Docs checked (Gmail "Resolve errors", Calendar "Handle API errors"): 403 ``rateLimitExceeded`` and
``userRateLimitExceeded`` mean "retry with exponential backoff"; other 403s (``dailyLimitExceeded``,
``domainPolicy``, missing scopes) are not retryable. ``request_with_retry`` already retries 429.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from firra.http import ProviderError

RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
# 1 + 2 + 4 + 8 + 16 s of backoff before a call gives up and the task is retried.
ATTEMPTS = 6
# Seconds between two Gmail requests of one client (one sync run of one mailbox).
GMAIL_REQUEST_INTERVAL = 0.5
# The onboarding header scan: metadata reads cost ~5 units, so 10 a second (~3000 units/min) leaves
# room for the paced backfill under the 6000/min per-user limit.
GMAIL_SCAN_INTERVAL = 0.1


@dataclass
class Pacer:
    """Spaces calls at least ``interval`` seconds apart."""

    interval: float
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _next: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def wait(self) -> None:
        """Thread-safe: parallel callers get consecutive slots ``interval`` apart."""
        with self._lock:
            now = self.clock()
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            self.sleep(start - now)


def _reasons(body: Any) -> set[str]:
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return set()
    reasons = {e.get("reason") for e in error.get("errors", []) if isinstance(e, dict)}
    reasons |= {d.get("reason") for d in error.get("details", []) if isinstance(d, dict)}
    return {r for r in reasons if isinstance(r, str)}


def rate_limited(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False
    try:
        reasons = _reasons(response.json())
    except ValueError:
        return False
    return bool(reasons & RATE_LIMIT_REASONS) or "RATE_LIMIT_EXCEEDED" in reasons


def throttled(exc: ProviderError) -> bool:
    """A call that still hit a rate limit after its retries (429, or Google's throttling 403)."""
    if exc.status == 429:
        return True
    return exc.status == 403 and (
        exc.reason in RATE_LIMIT_REASONS or exc.reason == "RATE_LIMIT_EXCEEDED"
    )
