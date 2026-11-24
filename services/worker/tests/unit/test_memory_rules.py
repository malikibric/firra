"""Rules that determine account memory without database or model calls."""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from firra.memory.rules import (
    ChampionCandidate,
    card_changed,
    choose_champion,
    commitment_status,
    dedupe_key,
    has_stated_budget,
    is_decision_maker,
    load_config,
    stakeholder_change_supported,
    title_from_signature,
)
from firra.memory.unknowns import CardFacts, unknowns


def test_signature_title_and_decision_maker() -> None:
    cfg = load_config()
    assert (
        title_from_signature("Jane Doe\nVP Marketing, Acme Corp\n+1 555 010", " jane   doe ")
        == "VP Marketing"
    )
    assert (
        title_from_signature("Jane Doe\nHead of Growth | Acme Corp", "Jane Doe") == "Head of Growth"
    )
    for bad in ("+1 555 010 0100", "jane@acme.com", "https://acme.com", "a" * 121):
        assert title_from_signature(f"Jane Doe\n{bad}", "Jane Doe") is None
    assert title_from_signature("Jane\nVP Marketing", "Jane Doe") is None
    assert is_decision_maker("co-founder", cfg)
    assert is_decision_maker("VP Marketing", cfg)
    assert not is_decision_maker("development", cfg)


def test_stakeholder_change_requires_quote_supported_fields() -> None:
    person_id = uuid4()
    quote = "Victor Hale joined as VP Marketing this month."

    def supported(name: str, title: str) -> bool:
        return stakeholder_change_supported(
            person_name=name,
            role_title=title,
            quote=quote,
            change="joined",
            author_person_id=None,
            matched_person_id=None,
        )

    assert supported("Victor Hale", "VP Marketing")
    assert not supported("Maria Santos", "VP Marketing")
    assert not supported("Victor Hale", "Chief Executive Officer")
    assert not supported("Vic", "VP Marketing")

    def departure_supported(author_person_id: UUID) -> bool:
        return stakeholder_change_supported(
            person_name="Victor Hale",
            role_title=None,
            quote="My last day at Corvid will be August 21.",
            change="left",
            author_person_id=author_person_id,
            matched_person_id=person_id,
        )

    assert departure_supported(person_id)
    assert not departure_supported(uuid4())


def test_dedupe_status_and_card_version() -> None:
    quote = "Please send the weekly report by Friday."
    assert dedupe_key(None, quote) == dedupe_key(
        None, " please  SEND the weekly report by Friday. "
    )
    assert dedupe_key(uuid4(), quote) != dedupe_key(uuid4(), quote)
    assert commitment_status("open", date(2026, 9, 1), date(2026, 9, 2)) == "late"
    assert commitment_status("open", date(2026, 9, 2), date(2026, 9, 2)) == "open"
    assert commitment_status("done", date(2026, 9, 1), date(2026, 9, 2)) == "done"
    assert commitment_status("cancelled", date(2026, 9, 1), date(2026, 9, 2)) == "cancelled"
    assert not card_changed(
        {"as_of": "2026-09-01", "goals": []}, {"as_of": "2026-09-02", "goals": []}
    )
    assert card_changed({"goals": []}, {"goals": [1]})
    assert has_stated_budget("Our budget is $20,000 for the next quarter.")
    assert not has_stated_budget("We should revisit our budget next quarter.")
    assert not has_stated_budget("The retainer is $20,000 each month.")


def test_champion_tie_departure_and_minimum() -> None:
    cfg = load_config()
    first, departed = uuid4(), uuid4()
    candidates = [
        ChampionCandidate(first, 3, datetime(2026, 1, 1, tzinfo=UTC)),
        ChampionCandidate(departed, 3, datetime(2025, 1, 1, tzinfo=UTC)),
    ]
    assert choose_champion(candidates, cfg) == departed
    assert choose_champion([ChampionCandidate(first, 2, candidates[0].first_seen_at)], cfg) is None


def test_every_unknown_code_toggles() -> None:
    cfg = load_config()
    as_of = date(2026, 9, 29)
    missing = CardFacts(False, False, None, None, None, None, False)
    assert {u["code"] for u in unknowns(missing, as_of=as_of, cfg=cfg)} == {
        "no_stated_goal",
        "no_decision_maker",
        "unknown_renewal_date",
        "no_result_90d",
        "no_champion_contact_30d",
        "budget_unknown",
    }
    complete = CardFacts(
        True,
        True,
        as_of,
        datetime(2026, 9, 1, tzinfo=UTC),
        "person",
        datetime(2026, 9, 20, tzinfo=UTC),
        True,
    )
    assert unknowns(complete, as_of=as_of, cfg=cfg) == []
