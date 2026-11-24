"""Minimal Microsoft Graph (v1.0) client: authorized GETs and OData paging.

Every request asks for immutable ids (``Prefer: IdType="ImmutableId"``) so message and event ids
stay stable when the user moves items between folders. Throttling (429 + Retry-After) is handled
by ``request_with_retry``.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from firra.http import ProviderError, request_with_retry

GRAPH = "https://graph.microsoft.com/v1.0"


class SyncStateExpiredError(Exception):
    """Graph no longer knows the stored delta state (410 Gone); start a fresh round."""


class ConflictError(Exception):
    """Graph refused a create because an equal resource exists (e.g. a duplicate subscription)."""


class NotFoundError(Exception):
    """The requested Graph resource does not exist (e.g. a well-known folder the mailbox lacks)."""


@dataclass(frozen=True)
class GraphPage:
    items: list[dict[str, Any]]
    next_link: str | None
    delta_link: str | None


@dataclass
class GraphClient:
    client: httpx.Client
    access_token: str
    user_path: str
    prefer: tuple[str, ...] = ()

    def url(self, path: str) -> str:
        return f"{GRAPH}{self.user_path}{path}"

    def request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Paging links come back from Graph and are stored in job progress/cursors: follow them
        # only to Graph itself.
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != "graph.microsoft.com":
            raise ProviderError(0)
        response = request_with_retry(
            self.client,
            method,
            url,
            params=params,
            json=json,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Prefer": ", ".join(('IdType="ImmutableId"', *self.prefer)),
            },
        )
        if response.status_code == 410:
            raise SyncStateExpiredError
        if response.status_code == 404:
            raise NotFoundError
        if response.status_code == 409:
            raise ConflictError
        if response.status_code >= 400:
            raise ProviderError.from_response(response)
        body: dict[str, Any] = response.json() if response.content else {}
        return body

    def get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", url, params)

    def page(self, url: str, params: dict[str, Any] | None = None) -> GraphPage:
        body = self.get(url, params)
        return GraphPage(
            items=list(body.get("value", [])),
            next_link=body.get("@odata.nextLink"),
            delta_link=body.get("@odata.deltaLink"),
        )


def graph_datetime(value: str) -> datetime:
    """Parses Graph timestamps (up to 7 fractional digits, ``Z`` or no zone meaning UTC)."""
    head, dot, tail = value.strip().partition(".")
    if dot:
        digits = "".join(c for c in tail if c.isdigit())
        value = f"{head}.{digits[:6]}{tail[len(digits) :]}"
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
