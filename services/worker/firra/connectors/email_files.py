"""Parses uploaded mail (.eml, .mbox) into canonical Messages. Content is untrusted data.

Standard library only (email, mailbox). Messages without a sender or a parseable date are
skipped and counted as invalid.
"""

import hashlib
import mailbox
from collections.abc import Iterator
from datetime import UTC
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from firra_contracts import Message, Participant
from firra_contracts.generated.records.message import Provider

MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_BODY_CHARS = 200_000
_PARSER = BytesParser(policy=policy.default)


class _TextExtractor(HTMLParser):
    """Visible text from HTML bodies; drops scripts, styles and markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor.text()


def _participants(values: list[str]) -> list[Participant]:
    people: list[Participant] = []
    for name, email in getaddresses(values):
        email = email.strip().lower()
        if "@" in email and 3 <= len(email) <= 320:
            people.append(Participant(email=email, name=(name.strip()[:200] or None)))
    return people


def _body_text(message: EmailMessage) -> str | None:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return None
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        return None
    if not isinstance(content, str):
        return None
    text = html_to_text(content) if part.get_content_type() == "text/html" else content
    return text[:MAX_BODY_CHARS]


def _header(message: EmailMessage, name: str) -> str | None:
    value = message.get(name)
    return str(value).strip()[:1000] if value else None


def parse_message(raw: bytes) -> Message | None:
    if len(raw) > MAX_MESSAGE_BYTES:
        return None
    message = _PARSER.parsebytes(raw)
    senders = _participants([str(v) for v in message.get_all("from", [])])
    try:
        occurred_at = parsedate_to_datetime(str(message.get("date", "")))
    except (TypeError, ValueError, IndexError):
        return None
    if not senders or occurred_at is None:
        return None
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)

    message_id = _header(message, "message-id")
    references = (_header(message, "references") or "").split()
    in_reply_to = _header(message, "in-reply-to")
    external_id = message_id or "sha256:" + hashlib.sha256(raw).hexdigest()
    thread_key = references[0] if references else (in_reply_to or message_id)

    return Message(
        provider=Provider.email_upload,
        external_id=external_id[:512],
        thread_key=thread_key[:512] if thread_key else None,
        occurred_at=occurred_at,
        sender=senders[0],
        to=_participants([str(v) for v in message.get_all("to", [])]),
        cc=_participants([str(v) for v in message.get_all("cc", [])]),
        bcc=_participants([str(v) for v in message.get_all("bcc", [])]),
        subject=(_header(message, "subject") or None),
        body_text=_body_text(message),
        message_id_header=message_id,
        in_reply_to=in_reply_to,
        deep_link=None,
    )


def iter_mbox(path: Path) -> Iterator[bytes]:
    box = mailbox.mbox(path, create=False)
    try:
        for key in box.iterkeys():
            yield box.get_bytes(key)
    finally:
        box.close()
