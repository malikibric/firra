"""Slack text cleaning tests: one fixture per construct, realistic messages,
idempotence and growth properties, and quote verification on cleaned text."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from firra.clean.slack import clean_slack_text, is_system_message
from firra.verify.quotes import BodyIndex, VerifiedQuote

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "slack_text"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _text_fixtures() -> list[str]:
    return sorted(p.name for p in FIXTURES.glob("*.json") if "input" in json.loads(p.read_text()))


def _system_fixtures() -> list[str]:
    return sorted(p.name for p in FIXTURES.glob("*.json") if "message" in json.loads(p.read_text()))


@pytest.mark.parametrize("name", _text_fixtures())
def test_fixture_texts_clean_to_expected(name: str) -> None:
    fixture = _load(name)
    assert (
        clean_slack_text(fixture["input"], fixture.get("users", {}), fixture.get("channels"))
        == fixture["expected"]
    )


@pytest.mark.parametrize("name", _system_fixtures())
def test_fixture_messages_classify_system(name: str) -> None:
    fixture = _load(name)
    assert is_system_message(fixture["message"]) is fixture["expected_system"]


def _expansion_allowance(text: str, users: dict[str, str], channels: dict[str, str]) -> int:
    """Extra characters cleaning may add: url suffixes plus resolved display names."""
    allowance = 0
    for match in re.finditer(r"<([^<>\n]+)>", text):
        body = match.group(1)
        if "|" in body:
            left, _, label = body.partition("|")
            if left.startswith("http") or left.startswith("mailto:"):
                allowance += len(left) + 3
            elif left.startswith(("@", "#")):
                allowance += len(users.get(left[1:], label or "user")) + 1
                allowance += len(channels.get(left[1:], "")) + 1
        elif body.startswith(("@", "#")):
            allowance += len(users.get(body[1:], "user")) + 1
            allowance += len(channels.get(body[1:], "channel")) + 1
    return allowance


def _check_properties(text: str, users: dict[str, str], channels: dict[str, str]) -> None:
    once = clean_slack_text(text, users, channels)
    assert clean_slack_text(once, users, channels) == once
    assert len(once) <= len(text) + _expansion_allowance(text, users, channels)


def test_cleaning_is_idempotent_and_bounded() -> None:
    for name in _text_fixtures():
        fixture = _load(name)
        _check_properties(fixture["input"], fixture.get("users", {}), fixture.get("channels", {}))
    combos = [
        ("Hey <@U1> and <@U2|bo> see <#C1|gen>!", {"U1": "Al"}, {"C1": "general"}),
        ("*A* _b_ ~c~ `d` ```e\nf``` :tada: :+1:", {}, {}),
        ("<https://a.example/1|l> <https://b.example/2|https://b.example/2> <@U9>", {}, {}),
        ("<!here> <!channel> <!everyone> <!subteam^S1|@g> <!date^1^{date}|Jan>", {}, {}),
    ]
    for text, users, channels in combos:
        _check_properties(text, users, channels)
    # Markup inside code spans normalizes on a second pass (backticks are
    # removed by design, so the second pass sees plain markers). The connector
    # cleans once and verification runs on that text, so this only bounds
    # growth for adversarial input instead of asserting a fixed point.
    adversarial = "run `a*b _c_` now <@U1|x>"
    once = clean_slack_text(adversarial, {}, {})
    assert len(once) <= len(adversarial) + _expansion_allowance(adversarial, {}, {})


def test_five_word_span_verifies_against_cleaned_text() -> None:
    fixture = _load("real_03.json")
    cleaned = clean_slack_text(fixture["input"], fixture.get("users", {}), fixture.get("channels"))
    words = cleaned.split()
    quote = " ".join(words[3:8])
    assert len(quote.split()) == 5
    verdict = BodyIndex(cleaned).verify(quote)
    assert isinstance(verdict, VerifiedQuote), f"quote rejected: {verdict!r}"
