from datetime import UTC, datetime
from uuid import uuid4

import pytest

from firra.signals.config import KNOWN_RULES, SignalsConfig, load_config
from firra.signals.engine import SignalsConfigError, evaluate, validate_config
from firra.signals.facts import ClientFacts, ExtractionFact
from firra.signals.model import SignalDraft
from firra.signals.rules import RULES
from firra.signals.rules.base import RuleConfig, RuleSpec

REF = datetime(2026, 9, 30, tzinfo=UTC)
EVIDENCE = uuid4()


def _facts(**overrides: object) -> ClientFacts:
    base: dict[str, object] = {
        "client_id": uuid4(),
        "status": "active",
        "policy": "full",
        "contract_start": None,
        "renewal_date": None,
        "notice_period_days": None,
        "ref": REF,
        "extractions": (
            ExtractionFact(
                id=uuid4(),
                record_id=uuid4(),
                kind="tone",
                payload={},
                occurred_at=REF,
                author_person_id=None,
                evidence_ids=(EVIDENCE,),
            ),
        ),
    }
    base.update(overrides)
    return ClientFacts(**base)  # type: ignore[arg-type]


def _config(**rules: dict[str, object]) -> SignalsConfig:
    return SignalsConfig(
        version="test",
        rules={
            name: {"enabled": name in rules, "family": "sentiment", **rules.get(name, {})}
            for name in KNOWN_RULES
        },
    )


class _Enabled(RuleConfig):
    pass


def _spec(*drafts: SignalDraft) -> dict[str, RuleSpec]:
    return {"tone_decline": RuleSpec(_Enabled, lambda facts, cfg: list(drafts))}


def _draft(**overrides: object) -> SignalDraft:
    base: dict[str, object] = {
        "type": "tone_decline",
        "severity": "high",
        "occurred_at": REF,
        "basis": "evidence",
        "dedupe_key": "tone",
        "evidence_ids": (EVIDENCE,),
    }
    base.update(overrides)
    return SignalDraft(**base)  # type: ignore[arg-type]


def test_shipped_config_loads_and_validates() -> None:
    config = load_config()
    validate_config(config)
    assert config.version == "signals.v3"
    assert {name for name, params in config.rules.items() if params["enabled"]} == set(RULES)


def test_config_rejects_unknown_rules_missing_rules_and_missing_switches() -> None:
    rules = {name: {"enabled": False} for name in KNOWN_RULES}
    with pytest.raises(ValueError, match="unknown signal rules"):
        SignalsConfig(version="x", rules={**rules, "made_up": {"enabled": False}})
    with pytest.raises(ValueError, match="missing from config"):
        SignalsConfig(version="x", rules={"tone_decline": {"enabled": False}})
    with pytest.raises(ValueError, match="explicit enabled"):
        SignalsConfig(version="x", rules={**rules, "in_house_risk": {}})


def test_unknown_rule_parameter_is_an_error() -> None:
    config = _config(reduced_engagement={"window_days": 30, "typo_days": 1})
    with pytest.raises(ValueError):
        validate_config(config)


def test_enabled_but_unimplemented_rule_is_an_error() -> None:
    registry = {name: spec for name, spec in RULES.items() if name != "in_house_risk"}
    with pytest.raises(SignalsConfigError, match="in_house_risk"):
        validate_config(_config(in_house_risk={}), registry)


@pytest.mark.parametrize(
    ("draft", "reason"),
    [
        (_draft(evidence_ids=()), "no_evidence"),
        (_draft(evidence_ids=(uuid4(),)), "foreign_evidence"),
        (_draft(basis="metadata", inputs={"n": 1}), "evidence_required_type"),
        (_draft(type="reduced_engagement", basis="metadata", evidence_ids=()), "no_inputs"),
        (
            _draft(type="reduced_engagement", basis="metadata", inputs={"n": 1}),
            "metadata_with_evidence",
        ),
    ],
)
def test_invalid_drafts_are_dropped_and_counted(draft: SignalDraft, reason: str) -> None:
    result = evaluate(_facts(), _config(tone_decline={}), _spec(draft))
    assert result.drafts == []
    assert dict(result.invalid) == {f"{draft.type}:{reason}": 1}


def test_valid_draft_passes_through() -> None:
    draft = _draft()
    assert evaluate(_facts(), _config(tone_decline={}), _spec(draft)).drafts == [draft]


def test_disabled_rules_do_not_run() -> None:
    result = evaluate(_facts(), _config(), _spec(_draft()))
    assert result.drafts == []


@pytest.mark.parametrize(("status", "policy"), [("prospect", "full"), ("active", "excluded")])
def test_prospects_and_excluded_clients_are_never_evaluated(status: str, policy: str) -> None:
    result = evaluate(
        _facts(status=status, policy=policy), _config(tone_decline={}), _spec(_draft())
    )
    assert result.drafts == [] and not result.invalid
