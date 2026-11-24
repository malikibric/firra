"""A need for a service the client already has is not an expansion signal."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from firra.signals.config import SignalsConfig, load_config
from firra.signals.engine import evaluate
from firra.signals.facts import ClientFacts, NeedFact

REF = datetime(2026, 9, 30, 12, tzinfo=UTC)


def _config() -> SignalsConfig:
    base = load_config()
    rules = {
        name: {**params, "enabled": name == "expansion_need"} for name, params in base.rules.items()
    }
    return SignalsConfig(version=base.version, rules=rules)


def _facts(service_name: str | None, tags: tuple[str, ...]) -> ClientFacts:
    return ClientFacts(
        client_id=uuid4(),
        status="active",
        policy="full",
        contract_start=None,
        renewal_date=None,
        notice_period_days=None,
        ref=REF,
        service_tags=tags,
        needs=(
            NeedFact(
                id=uuid4(),
                workspace_service_id=uuid4(),
                service_name=service_name,
                status="open",
                last_seen_at=REF - timedelta(days=5),
                mention_count=4,
                evidence_ids=(uuid4(),),
            ),
        ),
    )


def _types(facts: ClientFacts) -> list[str]:
    return [d.type for d in evaluate(facts, _config()).drafts]


def test_a_need_for_a_service_the_client_does_not_have_is_an_expansion() -> None:
    assert _types(_facts("Attribution & analytics", ("SEO & content",))) == ["expansion_need"]


@pytest.mark.parametrize("tag", ["SEO & content", "seo & CONTENT", "  seo   &  content "])
def test_a_need_for_a_service_the_client_already_has_raises_nothing(tag: str) -> None:
    assert _types(_facts("SEO & content", (tag,))) == []


def test_without_tags_or_a_service_name_nothing_is_excluded() -> None:
    assert _types(_facts("SEO & content", ())) == ["expansion_need"]
    assert _types(_facts(None, ("SEO & content",))) == ["expansion_need"]


def test_buys_compares_names_not_substrings() -> None:
    facts = _facts("SEO", ("SEO & content",))
    assert not facts.buys("SEO")
    assert facts.buys("seo & content")
    assert not facts.buys(None)
