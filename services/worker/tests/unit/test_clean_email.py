from pathlib import Path

import pytest

from firra.clean import clean_email, normalize_whitespace

FIXTURES = Path(__file__).parent / "fixtures" / "clean"
CASES = sorted(p.name.removesuffix(".in.txt") for p in FIXTURES.glob("*.in.txt"))


def _case(name: str) -> tuple[str, str]:
    source = (FIXTURES / f"{name}.in.txt").read_text()
    expected = (FIXTURES / f"{name}.out.txt").read_text().rstrip("\n")
    return source, expected


def test_fixtures_are_present() -> None:
    assert len(CASES) >= 10


@pytest.mark.parametrize("name", CASES)
def test_clean_fixture(name: str) -> None:
    source, expected = _case(name)
    assert clean_email(source).body == expected


@pytest.mark.parametrize("name", CASES)
def test_clean_is_idempotent(name: str) -> None:
    once = clean_email(_case(name)[0]).body
    assert clean_email(once).body == once


def test_signature_is_returned_separately() -> None:
    cleaned = clean_email(_case("signature_delimiter")[0])
    assert cleaned.signature == "Jane Doe\nVP Marketing, Acme Corp\n+1 555 010 0100"


def test_trailing_contact_block_is_returned_as_signature() -> None:
    cleaned = clean_email(_case("trailing_contacts")[0])
    assert cleaned.signature == "Acme Corp | www.acme.com | +1 555 010 0100\njane@acme.com"


def test_no_signature() -> None:
    assert clean_email("We need the report by Friday.").signature is None


def test_single_trailing_url_is_content() -> None:
    body = "Here is the brief for the new campaign:\nhttps://docs.example.com/brief"
    assert clean_email(body).body == body


def test_reply_header_needs_a_time_or_year() -> None:
    body = "On the call you wrote:\nthe launch moves to spring."
    assert clean_email(body).body == body


def test_reply_header_not_joined_across_paragraphs() -> None:
    body = "On it.\n\nOn Tue, Sep 1, 2026 at 10:00 AM Ana <ana@northbeam.agency> wrote:\n> old"
    assert clean_email(body).body == "On it."


def test_only_quoted_history_gives_empty_body() -> None:
    body = "On Tue, Sep 1, 2026 at 10:00 AM Ana <ana@northbeam.agency> wrote:\n> old text"
    assert clean_email(body).body == ""


def test_whitespace_normalization() -> None:
    assert normalize_whitespace("a \r\nb\r\n\r\n\r\n\r\nc  ") == "a\nb\n\nc"
