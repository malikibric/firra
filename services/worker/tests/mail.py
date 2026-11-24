"""Builds synthetic emails and mbox files for tests (fictional Northbeam Growth agency)."""

import mailbox
import tempfile
from email.message import EmailMessage
from pathlib import Path


def email_bytes(
    *,
    sender: str,
    to: list[str],
    cc: list[str] | None = None,
    subject: str = "Status update",
    body: str = "Hello, here is the update.",
    html: str | None = None,
    date: str | None = "Tue, 01 Sep 2026 10:00:00 +0000",
    message_id: str | None = "<m1@acme.com>",
    references: str | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    if date:
        message["Date"] = date
    if message_id:
        message["Message-ID"] = message_id
    if references:
        message["References"] = references
    if html is not None:
        message.set_content(html, subtype="html")
    else:
        message.set_content(body)
    return message.as_bytes()


def mbox_bytes(messages: list[bytes]) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "export.mbox"
        box = mailbox.mbox(path)
        for raw in messages:
            box.add(raw)
        box.flush()
        box.close()
        return path.read_bytes()
