import time

import httpx
import pytest

from firra.connectors.google.limits import Pacer, rate_limited


def _error(reason: str, status: int = 403) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": status, "errors": [{"reason": reason}], "message": "x"}}
    )


@pytest.mark.parametrize("reason", ["rateLimitExceeded", "userRateLimitExceeded"])
def test_per_user_rate_and_concurrency_limits_are_retryable(reason: str) -> None:
    assert rate_limited(_error(reason))


def test_structured_error_details_are_recognised() -> None:
    body = {"error": {"code": 403, "details": [{"reason": "RATE_LIMIT_EXCEEDED"}]}}
    assert rate_limited(httpx.Response(403, json=body))


@pytest.mark.parametrize(
    "reason", ["dailyLimitExceeded", "domainPolicy", "insufficientPermissions"]
)
def test_other_forbidden_errors_are_not_retried(reason: str) -> None:
    assert not rate_limited(_error(reason))


def test_non_403_and_non_json_responses_are_not_rate_limits() -> None:
    assert not rate_limited(_error("rateLimitExceeded", status=400))
    assert not rate_limited(httpx.Response(403, text="<html>forbidden</html>"))


def test_pacer_spaces_calls_by_the_interval() -> None:
    now = [100.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    pacer = Pacer(0.5, clock=lambda: now[0], sleep=sleep)
    pacer.wait()  # first call: no wait
    now[0] += 0.2
    pacer.wait()  # 0.3 s early
    now[0] += 2.0
    pacer.wait()  # long gap: no wait
    assert sleeps == [pytest.approx(0.3)]


def test_pacer_is_thread_safe_and_spaces_parallel_callers() -> None:
    import itertools
    import threading

    starts: list[float] = []
    pacer = Pacer(0.05)
    lock = threading.Lock()

    def call() -> None:
        pacer.wait()
        with lock:
            starts.append(time.monotonic())

    threads = [threading.Thread(target=call) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    starts.sort()
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    assert len(starts) == 6 and min(gaps) >= 0.04  # no two callers share a slot
