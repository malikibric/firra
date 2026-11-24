"""Gmail API (v1) over HTTPS: list, fetch raw messages, and read history for incremental sync.

Docs checked: users.messages.list (q, pageToken, maxResults<=500), users.messages.get
(format=raw -> base64url RFC 822 incl. attachments; format=metadata + repeated metadataHeaders ->
payload.headers only), users.history.list (startHistoryId; 404 when the id is too old),
users.getProfile (historyId), users.watch (topicName; renew at least every 7 days, daily
recommended). Quota: per-user rate and concurrency limits answered with 429 or 403
rateLimitExceeded/userRateLimitExceeded, both retried with backoff (see ``limits``).
"""

import base64
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from firra.connectors.email_files import parse_message
from firra.connectors.google import limits
from firra.http import ProviderError, request_with_retry
from firra_contracts import Message
from firra_contracts.generated.records.message import Provider

API = "https://gmail.googleapis.com/gmail/v1/users/me"
# Spam, trash, chats and drafts are never ingested.
EXCLUDE = "-in:spam -in:trash -in:chats -in:drafts"
# What minimization needs: participants and date (Message-ID etc. only for threading).
METADATA_HEADERS = ("From", "To", "Cc", "Bcc", "Date", "Message-ID", "In-Reply-To", "References")
_METADATA_NAMES = frozenset(h.lower() for h in METADATA_HEADERS)


class HistoryExpiredError(Exception):
    """The stored historyId is too old; the connection needs a fresh cursor."""


@dataclass(frozen=True)
class Page:
    ids: list[str]
    next_page_token: str | None


@dataclass(frozen=True)
class HistoryPage:
    added: list[str]
    deleted: list[str]
    history_id: str | None
    next_page_token: str | None


MAX_QUERY_DOMAINS = 40


def window_query(start: datetime, end: datetime, domains: Iterable[str] = ()) -> str:
    """Date window, narrowed to mail that names one of the mapped client domains.

    Gmail matches ``from:acme.com`` loosely (a superset); minimization at ingest stays the exact
    filter. Past ``MAX_QUERY_DOMAINS`` the narrowing is dropped rather than building a huge query.
    """
    wanted = sorted(set(domains))
    narrow = ""
    if 0 < len(wanted) <= MAX_QUERY_DOMAINS:
        narrow = " {" + " ".join(f"from:{d} to:{d} cc:{d}" for d in wanted) + "}"
    return f"after:{int(start.timestamp())} before:{int(end.timestamp())}{narrow} {EXCLUDE}"


def _header_block(headers: list[dict[str, Any]]) -> bytes:
    """RFC 822 header block from ``format=metadata`` headers, parsed like a raw message."""
    lines = []
    for header in headers:
        name, value = str(header.get("name", "")), str(header.get("value", ""))
        if name.lower() in _METADATA_NAMES:
            # Values arrive unfolded; a stray line break must not start a new header.
            value = value.replace("\r", " ").replace("\n", " ")
            lines.append(f"{name}: {value}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


@dataclass
class GmailClient:
    client: httpx.Client
    access_token: str
    pacer: limits.Pacer = field(default_factory=lambda: limits.Pacer(limits.GMAIL_REQUEST_INTERVAL))
    _headers: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self._headers = {"Authorization": f"Bearer {self.access_token}"}

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self.pacer.wait()
        response = request_with_retry(
            self.client,
            "GET",
            f"{API}{path}",
            params=params,
            headers=self._headers,
            attempts=limits.ATTEMPTS,
            retry_if=limits.rate_limited,
        )
        if response.status_code == 404 and path == "/history":
            raise HistoryExpiredError
        if response.status_code >= 400:
            raise ProviderError.from_response(response)
        body: dict[str, Any] = response.json()
        return body

    def list_ids(self, query: str, page_token: str | None) -> Page:
        params: dict[str, Any] = {"q": query, "maxResults": 500}
        if page_token:
            params["pageToken"] = page_token
        body = self._get("/messages", params)
        return Page(
            ids=[str(m["id"]) for m in body.get("messages", [])],
            next_page_token=body.get("nextPageToken"),
        )

    def message(
        self, message_id: str, *, headers_only: bool = False, not_found_ok: bool = False
    ) -> Message | None:
        """The message, or with ``headers_only`` just its participants and date (no body).

        With ``not_found_ok`` a message that no longer exists (history names messages that were
        deleted or moved before they could be read) is ``None`` instead of a failed sync.

        Headers are a few hundred bytes; ``format=raw`` includes every attachment. Fetching headers
        first lets minimization drop non-client mail without downloading it.
        """
        params: dict[str, Any] = (
            {"format": "metadata", "metadataHeaders": list(METADATA_HEADERS)}
            if headers_only
            else {"format": "raw"}
        )
        try:
            body = self._get(f"/messages/{message_id}", params)
        except ProviderError as error:
            if not_found_ok and error.status == 404:
                return None
            raise
        labels = set(body.get("labelIds", []))
        if labels & {"SPAM", "TRASH", "DRAFT", "CHAT"}:
            return None
        if headers_only:
            raw = _header_block(body.get("payload", {}).get("headers", []))
        else:
            raw = base64.urlsafe_b64decode(str(body["raw"]) + "=" * (-len(str(body["raw"])) % 4))
        parsed = parse_message(raw)
        if parsed is None:
            return None
        return parsed.model_copy(
            update={
                "provider": Provider.gmail,
                "external_id": message_id,
                "thread_key": str(body.get("threadId") or parsed.thread_key or message_id),
                "deep_link": f"https://mail.google.com/mail/u/0/#all/{message_id}",
            }
        )

    def watch(self, topic: str) -> int:
        """Starts or renews push notifications to ``topic``; returns the expiry (epoch ms)."""
        self.pacer.wait()
        response = request_with_retry(
            self.client,
            "POST",
            f"{API}/watch",
            json={"topicName": topic},
            headers=self._headers,
            attempts=limits.ATTEMPTS,
            retry_if=limits.rate_limited,
        )
        if response.status_code >= 400:
            raise ProviderError.from_response(response)
        return int(response.json()["expiration"])

    def history_id(self) -> str:
        return str(self._get("/profile", {})["historyId"])

    def history(self, start_history_id: str, page_token: str | None) -> HistoryPage:
        params: dict[str, Any] = {
            "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded", "messageDeleted"],
            "maxResults": 500,
        }
        if page_token:
            params["pageToken"] = page_token
        body = self._get("/history", params)
        added: list[str] = []
        deleted: list[str] = []
        for entry in body.get("history", []):
            added += [str(m["message"]["id"]) for m in entry.get("messagesAdded", [])]
            deleted += [str(m["message"]["id"]) for m in entry.get("messagesDeleted", [])]
        return HistoryPage(
            added=added,
            deleted=deleted,
            history_id=body.get("historyId"),
            next_page_token=body.get("nextPageToken"),
        )


def iter_pages(client: GmailClient, query: str, page_token: str | None) -> Iterator[Page]:
    while True:
        page = client.list_ids(query, page_token)
        yield page
        if not page.next_page_token:
            return
        page_token = page.next_page_token
