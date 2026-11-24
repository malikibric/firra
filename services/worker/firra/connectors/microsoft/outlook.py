"""Outlook mail over Microsoft Graph: mailbox-wide listing by received time.

Docs checked: ``GET /me/messages`` lists the whole mailbox (including Deleted Items); $filter and
$orderby on receivedDateTime (the ordered property must lead the filter, else InefficientFilter);
$top 1..1000; ``Prefer: outlook.body-content-type="text"`` (bodies may still arrive as HTML, which
we convert). Message delta (``/mailFolders/{id}/messages/delta``) is per folder only, so instead of
tracking every folder the incremental sync follows a receivedDateTime watermark (see sync handler).
Junk, Deleted Items and Drafts are never ingested.
Docs checked (2026-10-02): GET /me/messages/{id}, $select, text body preference:
https://learn.microsoft.com/en-us/graph/api/message-get?view=graph-rest-1.0
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

from firra.connectors.email_files import MAX_BODY_CHARS, html_to_text
from firra.connectors.microsoft.graph import GraphClient, NotFoundError, graph_datetime
from firra_contracts import Message, Participant
from firra_contracts.generated.records.message import Provider

PREFER_TEXT = 'outlook.body-content-type="text"'
EXCLUDED_FOLDERS = ("junkemail", "deleteditems", "drafts")
PAGE_SIZE = 100
_SELECT = ",".join(
    (
        "id",
        "conversationId",
        "internetMessageId",
        "receivedDateTime",
        "sentDateTime",
        "from",
        "toRecipients",
        "ccRecipients",
        "bccRecipients",
        "subject",
        "body",
        "isDraft",
        "parentFolderId",
        "webLink",
    )
)


@dataclass(frozen=True)
class MailPage:
    messages: list[Message]
    skipped: int
    next_link: str | None
    # Latest receivedDateTime on the page (the incremental watermark), if any.
    latest_received: datetime | None


def _participant(raw: dict[str, Any] | None) -> Participant | None:
    address = ((raw or {}).get("emailAddress") or {}).get("address") or ""
    email = str(address).strip().lower()
    # Exchange can report internal senders by X.500 address only; those are not emails.
    if "@" not in email or not 3 <= len(email) <= 320:
        return None
    name = str(((raw or {}).get("emailAddress") or {}).get("name") or "").strip()[:200] or None
    return Participant(email=email, name=name)


def _people(values: list[dict[str, Any]] | None) -> list[Participant]:
    return [p for p in (_participant(v) for v in values or []) if p is not None]


def _body(raw: dict[str, Any]) -> str | None:
    body = raw.get("body") or {}
    content = body.get("content")
    if not isinstance(content, str) or not content:
        return None
    text = html_to_text(content) if body.get("contentType") == "html" else content
    return text[:MAX_BODY_CHARS]


def to_message(raw: dict[str, Any], excluded_folders: frozenset[str]) -> Message | None:
    if raw.get("isDraft") or raw.get("parentFolderId") in excluded_folders:
        return None
    sender = _participant(raw.get("from"))
    stamp = raw.get("sentDateTime") or raw.get("receivedDateTime")
    if sender is None or not stamp:
        return None
    message_id = str(raw.get("internetMessageId") or "").strip()[:1000] or None
    return Message(
        provider=Provider.outlook,
        external_id=str(raw["id"])[:512],
        thread_key=str(raw.get("conversationId") or message_id or raw["id"])[:512],
        occurred_at=graph_datetime(str(stamp)),
        sender=sender,
        to=_people(raw.get("toRecipients")),
        cc=_people(raw.get("ccRecipients")),
        bcc=_people(raw.get("bccRecipients")),
        subject=(str(raw.get("subject") or "")[:2000] or None),
        body_text=_body(raw),
        message_id_header=message_id,
        in_reply_to=None,
        deep_link=(str(raw["webLink"])[:2000] if raw.get("webLink") else None),
    )


def received_filter(start: datetime, end: datetime | None) -> str:
    since = f"receivedDateTime ge {start.isoformat().replace('+00:00', 'Z')}"
    if end is None:
        return since
    return f"{since} and receivedDateTime lt {end.isoformat().replace('+00:00', 'Z')}"


@dataclass
class OutlookMailClient:
    graph: GraphClient

    def excluded_folder_ids(self) -> frozenset[str]:
        ids: set[str] = set()
        for name in EXCLUDED_FOLDERS:
            try:
                folder = self.graph.get(self.graph.url(f"/mailFolders/{name}"), {"$select": "id"})
                ids.add(str(folder["id"]))
            except NotFoundError:
                continue
        return frozenset(ids)

    def message(self, external_id: str, excluded_folders: frozenset[str]) -> Message | None:
        """Fetch a known immutable message id with the same parsing as mailbox sync."""
        raw = self.graph.get(
            self.graph.url(f"/messages/{quote(external_id, safe='')}"), {"$select": _SELECT}
        )
        return to_message(raw, excluded_folders)

    def page(
        self,
        start: datetime,
        end: datetime | None,
        next_link: str | None,
        excluded_folders: frozenset[str],
    ) -> MailPage:
        if next_link:
            page = self.graph.page(next_link)
        else:
            page = self.graph.page(
                self.graph.url("/messages"),
                {
                    "$filter": received_filter(start, end),
                    "$orderby": "receivedDateTime asc",
                    "$select": _SELECT,
                    "$top": PAGE_SIZE,
                },
            )
        messages: list[Message] = []
        skipped = 0
        latest: datetime | None = None
        for item in page.items:
            if item.get("receivedDateTime"):
                received = graph_datetime(str(item["receivedDateTime"]))
                latest = received if latest is None or received > latest else latest
            message = to_message(item, excluded_folders)
            if message is None:
                skipped += 1
            else:
                messages.append(message)
        return MailPage(messages, skipped, page.next_link, latest)
