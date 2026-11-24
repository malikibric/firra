import httpx
import pytest

from firra.http import ProviderError, error_reason, request_with_retry


def test_retries_rate_limits_and_respects_retry_after() -> None:
    statuses = iter([429, 503, 200])
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        return httpx.Response(status, headers={"Retry-After": "7"} if status == 429 else {})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = request_with_retry(client, "GET", "https://api.test/x", sleep=sleeps.append)
    assert response.status_code == 200
    assert sleeps == [7.0, 2.0]


def test_gives_up_after_the_attempt_limit_and_does_not_retry_client_errors() -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert (
        request_with_retry(
            client, "GET", "https://api.test/x", attempts=3, sleep=lambda _: None
        ).status_code
        == 503
    )
    assert len(calls) == 3

    four_oh_four = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(404)))
    assert (
        request_with_retry(
            four_oh_four, "GET", "https://api.test/x", sleep=lambda _: pytest.fail("no retry")
        ).status_code
        == 404
    )


def test_retry_if_extends_the_retryable_responses() -> None:
    statuses = iter([403, 403, 200])
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(next(statuses))))
    response = request_with_retry(
        client,
        "GET",
        "https://api.test/x",
        sleep=sleeps.append,
        retry_if=lambda r: r.status_code == 403,
    )
    assert response.status_code == 200
    assert sleeps == [1.0, 2.0]


def test_provider_errors_keep_only_enum_like_error_codes() -> None:
    google = httpx.Response(403, json={"error": {"errors": [{"reason": "rateLimitExceeded"}]}})
    graph = httpx.Response(403, json={"error": {"code": "ErrorAccessDenied", "message": "x"}})
    oauth = httpx.Response(400, json={"error": "invalid_grant"})
    free_text = httpx.Response(400, json={"error": {"code": "Mailbox ceo@acme.com not found"}})
    assert error_reason(google) == "rateLimitExceeded"
    assert error_reason(graph) == "ErrorAccessDenied"
    assert error_reason(oauth) == "invalid_grant"
    assert error_reason(free_text) is None
    assert error_reason(httpx.Response(500, text="<html>")) is None
    assert ProviderError.from_response(google).reason == "rateLimitExceeded"


def test_a_deadline_stops_retrying_instead_of_waiting_out_every_attempt() -> None:
    clock_now = [0.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock_now[0] += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        clock_now[0] += 5  # each attempt takes 5 s
        return httpx.Response(429, headers={"retry-after": "30"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = request_with_retry(
        client,
        "POST",
        "https://x.test",
        sleep=sleep,
        deadline_seconds=50,
        clock=lambda: clock_now[0],
    )
    # attempt 1 ends at 5 s, waits 30, attempt 2 ends at 40 s, another 30 s would pass 50 s: stop
    assert response.status_code == 429 and waits == [30.0]


def test_a_deadline_ends_transport_errors_too() -> None:
    clock_now = [0.0]

    def handler(request: httpx.Request) -> httpx.Response:
        clock_now[0] += 120
        raise httpx.ReadTimeout("slow", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ReadTimeout):
        request_with_retry(
            client,
            "POST",
            "https://x.test",
            sleep=lambda s: None,
            deadline_seconds=100,
            clock=lambda: clock_now[0],
        )
    assert clock_now[0] == 120  # one attempt took 120 s: no second one starts past the deadline


def test_without_a_deadline_every_attempt_is_used() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    request_with_retry(client, "GET", "https://x.test", sleep=lambda s: None)
    assert calls == 4
