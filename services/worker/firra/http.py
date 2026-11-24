"""Provider HTTP calls with bounded retries that respect Retry-After (CLAUDE.md § 9)."""

import re
import time
from collections.abc import Callable
from typing import Any

import httpx

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


# Provider error codes are enum-like identifiers (Google ``reason``, Graph ``code``); anything
# else is dropped so free text from an error body can never reach a log.
_REASON = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,63}$")


def error_reason(response: httpx.Response) -> str | None:
    """The provider's error code from a JSON error body (Google or Microsoft Graph), if any."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return error if isinstance(error, str) and _REASON.match(error) else None
    candidates = [e.get("reason") for e in error.get("errors", []) if isinstance(e, dict)]
    candidates += [error.get("code"), error.get("status")]
    for candidate in candidates:
        if isinstance(candidate, str) and _REASON.match(candidate):
            return candidate
    return None


class ProviderError(Exception):
    """A provider call failed after retries: HTTP status and error code only, never the body."""

    def __init__(self, status: int, reason: str | None = None) -> None:
        super().__init__(f"provider returned {status}")
        self.status = status
        self.reason = reason

    @classmethod
    def from_response(cls, response: httpx.Response) -> "ProviderError":
        return cls(response.status_code, error_reason(response))


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] | None = None,
    retry_if: Callable[[httpx.Response], bool] | None = None,
    deadline_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    **kwargs: Any,
) -> httpx.Response:
    """``retry_if`` marks further responses as retryable (providers that throttle with a 403).

    ``deadline_seconds`` bounds the time spent retrying: no further attempt starts once waiting for
    it would pass the deadline, so a call lasts at most the deadline plus one attempt's timeout.
    Without it, four attempts with a long timeout can outlast a task's delivery window.
    """
    wait = sleep or time.sleep
    started = clock()

    def out_of_time(delay: float) -> bool:
        return deadline_seconds is not None and clock() - started + delay >= deadline_seconds

    for attempt in range(1, attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError:
            delay = min(max_delay, base_delay * 2 ** (attempt - 1))
            if attempt == attempts or out_of_time(delay):
                raise
            wait(delay)
            continue
        retryable = response.status_code in RETRY_STATUSES or (
            retry_if is not None and retry_if(response)
        )
        if not retryable or attempt == attempts:
            return response
        retry_after = response.headers.get("retry-after", "")
        delay = min(
            max_delay,
            float(retry_after) if retry_after.isdigit() else base_delay * 2 ** (attempt - 1),
        )
        if out_of_time(delay):
            return response
        wait(delay)
    raise AssertionError("unreachable")  # pragma: no cover
