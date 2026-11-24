from datetime import UTC, datetime
from pathlib import Path

from firra.connectors.email_files import html_to_text, iter_mbox, parse_message
from tests.mail import email_bytes, mbox_bytes


def test_parses_addresses_dates_and_threading() -> None:
    message = parse_message(
        email_bytes(
            sender='"Carla CEO" <Carla@Acme.com>',
            to=["ana@northbeam.agency", "Ben <ben@northbeam.agency>"],
            cc=["pm@acme.com"],
            references="<root@acme.com> <m0@acme.com>",
        )
    )
    assert message is not None
    assert message.sender.email == "carla@acme.com"
    assert message.sender.name == "Carla CEO"
    assert [p.email for p in message.to] == ["ana@northbeam.agency", "ben@northbeam.agency"]
    assert [p.email for p in message.cc] == ["pm@acme.com"]
    assert message.occurred_at == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    assert message.external_id == "<m1@acme.com>"
    assert message.thread_key == "<root@acme.com>"
    assert message.body_text is not None and "here is the update" in message.body_text


def test_html_bodies_become_text_without_scripts() -> None:
    html = (
        "<html><head><style>p{}</style></head>"
        "<body><p>Hi&nbsp;team</p><script>x()</script></body></html>"
    )
    message = parse_message(email_bytes(sender="a@acme.com", to=["b@x.com"], html=html))
    assert message is not None and message.body_text is not None
    assert "Hi\xa0team" in message.body_text or "Hi team" in message.body_text
    assert "x()" not in message.body_text
    assert html_to_text("<b>bold</b><br>line") == "bold\nline"


def test_missing_sender_or_date_is_invalid() -> None:
    assert parse_message(email_bytes(sender="", to=["b@x.com"])) is None
    assert parse_message(email_bytes(sender="a@acme.com", to=["b@x.com"], date=None)) is None
    assert (
        parse_message(email_bytes(sender="a@acme.com", to=["b@x.com"], date="not a date")) is None
    )


def test_messages_without_message_id_get_a_stable_content_id() -> None:
    raw = email_bytes(sender="a@acme.com", to=["b@x.com"], message_id=None)
    first, second = parse_message(raw), parse_message(raw)
    assert first is not None and second is not None
    assert first.external_id.startswith("sha256:")
    assert first.external_id == second.external_id


def test_naive_dates_are_treated_as_utc() -> None:
    message = parse_message(
        email_bytes(sender="a@acme.com", to=["b@x.com"], date="Tue, 01 Sep 2026 10:00:00 -0000")
    )
    assert message is not None and message.occurred_at.tzinfo is not None


def test_iter_mbox_yields_every_message(tmp_path: Path) -> None:
    raws = [
        email_bytes(
            sender="a@acme.com",
            to=["b@x.com"],
            message_id=f"<m{i}@acme.com>",
            body="From the start\nFrom here on",
        )
        for i in range(3)
    ]
    path = tmp_path / "x.mbox"
    path.write_bytes(mbox_bytes(raws))
    parsed = [parse_message(raw) for raw in iter_mbox(path)]
    assert [m.external_id for m in parsed if m] == [
        "<m0@acme.com>",
        "<m1@acme.com>",
        "<m2@acme.com>",
    ]
    assert all(m and m.body_text and "From here on" in m.body_text for m in parsed)
