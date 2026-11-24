from uuid import UUID

import pytest

from firra.policy.minimize import (
    ClientRef,
    Directory,
    Drop,
    Keep,
    decide,
    email_domain,
    needs_content,
)

ACME = ClientRef(UUID(int=1), "Acme", "full")
GLOBEX = ClientRef(UUID(int=2), "Globex", "metadata_only")
SECRET = ClientRef(UUID(int=3), "Umbrella", "excluded")

DIRECTORY = Directory(
    client_domains={
        "acme.com": ACME,
        "acme.io": ACME,
        "globex.com": GLOBEX,
        "umbrella.org": SECRET,
    },
    firm_domains=frozenset({"northbeam.agency"}),
)
MENTIONS = Directory(
    client_domains=DIRECTORY.client_domains,
    firm_domains=DIRECTORY.firm_domains,
    internal_mentions=True,
)
FIRM_A = "ana@northbeam.agency"
FIRM_B = "ben@northbeam.agency"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Alice@ACME.com", "acme.com"),
        ("bob@eu.acme.com.", "eu.acme.com"),
        ("no-at-sign", None),
        ("@acme.com", None),
        ("x@localhost", None),
    ],
)
def test_email_domain(value: str, expected: str | None) -> None:
    assert email_domain(value) == expected


def test_client_email_is_kept_as_inbound() -> None:
    assert decide("ceo@acme.com", [FIRM_A], DIRECTORY) == Keep(ACME, "full", "in")


def test_firm_email_to_client_is_outbound_and_subdomains_match() -> None:
    assert decide(FIRM_A, ["cto@eu.acme.com"], DIRECTORY) == Keep(ACME, "full", "out")


def test_unmapped_email_is_dropped() -> None:
    assert decide("news@vendor.com", [FIRM_A], DIRECTORY) == Drop("no_client")


def test_personal_mailbox_domains_never_match() -> None:
    assert decide("someone@gmail.com", [FIRM_A], DIRECTORY) == Drop("no_client")


def test_lookalike_domains_do_not_match() -> None:
    assert decide("x@notacme.com", [FIRM_A], DIRECTORY) == Drop("no_client")
    assert decide("x@acme.com.evil.net", [FIRM_A], DIRECTORY) == Drop("no_client")


def test_any_excluded_participant_drops_the_record() -> None:
    assert decide(FIRM_A, ["ceo@acme.com", "cc@umbrella.org"], DIRECTORY) == Drop("excluded_client")


def test_most_restrictive_policy_wins_across_clients() -> None:
    decision = decide("ceo@acme.com", ["pm@globex.com", FIRM_A], DIRECTORY)
    assert decision == Keep(ACME, "metadata_only", "in")


def test_primary_client_is_the_one_with_most_participants_when_firm_sends() -> None:
    decision = decide(FIRM_A, ["a@globex.com", "b@globex.com", "c@acme.com"], DIRECTORY)
    assert isinstance(decision, Keep)
    assert decision.client == GLOBEX


def test_internal_mail_is_dropped_without_the_workspace_setting() -> None:
    assert decide(FIRM_A, [FIRM_B], DIRECTORY, mention_text="Acme renewal next week") == Drop(
        "no_client"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Acme renewal next week", Keep(ACME, "full", "internal")),
        ("Acme's renewal", Keep(ACME, "full", "internal")),
        ("check the acme.io launch", Keep(ACME, "full", "internal")),
        ("Globex wants a call", Keep(GLOBEX, "metadata_only", "internal")),
        ("Acme and Globex both asked", Drop("ambiguous_mention")),
        ("Umbrella escalation", Drop("excluded_client")),
        ("lunch on friday", Drop("no_client")),
        ("acmeish is not acme-corp", Drop("no_client")),
    ],
)
def test_internal_mentions(text: str, expected: object) -> None:
    assert decide(FIRM_A, [FIRM_B], MENTIONS, mention_text=text) == expected


def test_internal_mentions_do_not_apply_when_outsiders_are_present() -> None:
    assert decide(FIRM_A, ["x@vendor.com"], MENTIONS, mention_text="Acme") == Drop("no_client")


@pytest.mark.parametrize(
    ("sender", "others", "directory", "expected"),
    [
        ("ceo@acme.com", [FIRM_A], DIRECTORY, True),
        (FIRM_A, ["pm@globex.com"], DIRECTORY, True),  # metadata_only still needs word counts
        ("news@vendor.com", [FIRM_A], DIRECTORY, False),
        (FIRM_A, ["ceo@acme.com", "cc@umbrella.org"], DIRECTORY, False),
        (FIRM_A, [FIRM_B], DIRECTORY, False),
        (FIRM_A, [FIRM_B], MENTIONS, True),  # a client may be named in the body
        (FIRM_A, ["x@vendor.com"], MENTIONS, False),
    ],
)
def test_needs_content_only_for_mail_that_may_be_kept(
    sender: str, others: list[str], directory: Directory, expected: bool
) -> None:
    assert needs_content(sender, iter(others), directory) is expected
