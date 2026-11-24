from firra.connectors.email_files import parse_message
from firra.connectors.google.gmail import _header_block


def test_metadata_headers_parse_like_a_raw_message_without_a_body() -> None:
    block = _header_block(
        [
            {"name": "From", "value": "CEO <ceo@acme.com>"},
            {"name": "To", "value": "ana@northbeam.agency, ben@northbeam.agency"},
            {"name": "Date", "value": "Tue, 01 Sep 2026 10:00:00 +0000"},
            {"name": "Subject", "value": "not requested, not kept"},
        ]
    )
    message = parse_message(block)
    assert message is not None
    assert message.sender.email == "ceo@acme.com"
    assert [p.email for p in message.to] == ["ana@northbeam.agency", "ben@northbeam.agency"]
    assert message.subject is None and not message.body_text


def test_line_breaks_in_values_cannot_add_headers() -> None:
    block = _header_block(
        [
            {"name": "From", "value": "x@vendor.com\r\nCc: ceo@acme.com"},
            {"name": "Date", "value": "Tue, 01 Sep 2026 10:00:00 +0000"},
        ]
    )
    message = parse_message(block)
    assert message is not None and message.cc == []
