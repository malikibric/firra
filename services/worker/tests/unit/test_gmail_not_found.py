import httpx
import pytest

from firra.connectors.google.gmail import GmailClient
from firra.connectors.google.limits import Pacer
from firra.http import ProviderError


def client(status: int) -> GmailClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"errors": [{"reason": "notFound"}]}})

    return GmailClient(httpx.Client(transport=httpx.MockTransport(handler)), "token", Pacer(0))


def test_a_message_deleted_after_the_history_listed_it_is_skipped_not_fatal() -> None:
    assert client(404).message("gone", headers_only=True, not_found_ok=True) is None
    assert client(404).message("gone", not_found_ok=True) is None


def test_without_the_flag_a_missing_message_is_still_an_error() -> None:
    with pytest.raises(ProviderError) as caught:
        client(404).message("gone")
    assert caught.value.status == 404


def test_other_provider_errors_are_never_swallowed() -> None:
    with pytest.raises(ProviderError) as caught:
        client(403).message("x", headers_only=True, not_found_ok=True)
    assert caught.value.status == 403
