"""Runs the enabled rules for one client and drops drafts that would break an invariant.

Nothing here touches the database. A rule that is enabled in config but not implemented is a
configuration error, so a typo can never silently disable a signal.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from firra.signals.config import SignalsConfig
from firra.signals.facts import ClientFacts
from firra.signals.model import EVIDENCE_ONLY, SignalDraft
from firra.signals.rules import RULES
from firra.signals.rules.base import RuleConfig, RuleSpec


class SignalsConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Evaluation:
    drafts: list[SignalDraft]
    # Drafts dropped by validation, by reason (a rule bug, never stored).
    invalid: Counter[str] = field(default_factory=Counter)


def rule_params(config: SignalsConfig, name: str, spec: RuleSpec) -> dict[str, Any]:
    """The rule's YAML parameters plus the shared burst length for rules that count touches."""
    params = dict(config.rules[name])
    if "chat_burst_minutes" in spec.config_model.model_fields:
        params["chat_burst_minutes"] = config.chat_burst_minutes
    return params


def validate_config(config: SignalsConfig, rules: Mapping[str, RuleSpec] = RULES) -> None:
    """Fails when an enabled rule has no implementation or its parameters do not fit."""
    for name, params in config.rules.items():
        if not params["enabled"]:
            continue
        spec = rules.get(name)
        if spec is None:
            raise SignalsConfigError(f"rule {name} is enabled but not implemented")
        spec.config_model.model_validate(rule_params(config, name, spec))


def _problem(draft: SignalDraft, known_evidence: frozenset[UUID]) -> str | None:
    if draft.type in EVIDENCE_ONLY and draft.basis != "evidence":
        return "evidence_required_type"
    if draft.basis == "evidence":
        if not draft.evidence_ids:
            return "no_evidence"
        if not set(draft.evidence_ids) <= known_evidence:
            return "foreign_evidence"
    else:
        if draft.evidence_ids:
            return "metadata_with_evidence"
        if draft.inputs is None:
            return "no_inputs"
    return None


def evaluate(
    facts: ClientFacts, config: SignalsConfig, rules: Mapping[str, RuleSpec] = RULES
) -> Evaluation:
    drafts: list[SignalDraft] = []
    invalid: Counter[str] = Counter()
    # Prospects are not clients yet and excluded clients must never be read (product rules 1, 8).
    if facts.status == "prospect" or facts.policy == "excluded":
        return Evaluation(drafts=drafts, invalid=invalid)
    known = facts.evidence_ids()
    for name, spec in rules.items():
        if not config.rules[name]["enabled"]:
            continue
        rule_config: RuleConfig = spec.config_model.model_validate(rule_params(config, name, spec))
        for draft in spec.evaluate(facts, rule_config):
            problem = _problem(draft, known)
            if problem is None:
                drafts.append(draft)
            else:
                invalid[f"{draft.type}:{problem}"] += 1
    return Evaluation(drafts=drafts, invalid=invalid)
