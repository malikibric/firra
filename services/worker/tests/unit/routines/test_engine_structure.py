"""Generated safety properties, independent of T43c's importance rule cases."""

import ast
from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, timedelta
from pathlib import Path
from random import Random
from uuid import UUID

import pytest

from firra.assessment.config import RISK_ORDER
from firra.routines import engine
from firra.routines.config import load_config
from firra.routines.engine import apply_budget, classify, is_suppressed, merge, plan
from firra.routines.model import Candidate, ClientContext, Decision, Plan, Prior, Trigger

TODAY = date(2026, 10, 3)
TRIGGERS: tuple[Trigger, ...] = (
    "risk_up",
    "risk_down",
    "expansion_up",
    "signal_opened",
    "signal_resolved",
    "commitment_late",
    "notice_close",
)


def test_frozen_models() -> None:
    client = UUID(int=1)
    objects: list[Candidate | ClientContext | Prior | Decision | Plan] = [
        Candidate(client, "state_change", "risk_up", TODAY),
        ClientContext(client, "full", "stable", None),
        Prior("state_change", "fixture", None, TODAY, frozenset(), 0),
        Decision(client, "state_change", "fixture", "fyi", None, (), frozenset(), 0, None, TODAY),
        Plan((), {}),
    ]
    for obj in objects:
        field = fields(obj)[0].name
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field, None)
        assert not hasattr(obj, "__dict__")


@pytest.mark.parametrize("used", range(5))
def test_generated_safety_and_order_independence(used: int) -> None:
    config = load_config()
    contexts = {
        UUID(int=i + 1): ClientContext(
            UUID(int=i + 1),
            "full" if i % 2 else "metadata_only",
            RISK_ORDER[i % 4],
            None if i % 3 else i,
        )
        for i in range(24)
    }
    candidates = {
        client: [
            Candidate(
                client,
                "state_change",
                trigger,
                TODAY - timedelta(days=j),
                risk_to=context.risk,
                severity="high",
                evidence_ids=frozenset({UUID(int=j + 1)}),
            )
            for j, trigger in enumerate(TRIGGERS)
        ]
        for client, context in contexts.items()
    }
    missing = UUID(int=100)
    candidates[missing] = [Candidate(missing, "state_change", "risk_up", TODAY)]
    for client in contexts:
        candidates[client] += [
            replace(c, kind="expansion", evidence_ids=frozenset()) for c in candidates[client]
        ]
        candidates[client] += [
            replace(c, as_of=TODAY, evidence_ids=frozenset({UUID(int=50)}))
            for c in candidates[client]
        ]
    priors = {
        client: [
            Prior(
                "state_change",
                "risk_rose_elevated",
                base.verdict,
                TODAY,
                frozenset({UUID(int=i + 1)}),
                i % 4,
            )
            for i, base in enumerate(
                (
                    Prior("state_change", "", "wrong", TODAY, frozenset(), 0),
                    Prior("state_change", "", "not_now", TODAY, frozenset(), 0),
                    Prior("state_change", "", "already_known", TODAY, frozenset(), 0),
                )
            )
        ]
        for client in contexts
    }
    for client in contexts:
        merged = merge(candidates[client], contexts[client], config)
        assert merged is not None
        assert contexts[client].policy == "full" or not merged.evidence_ids
        priors[client] = [
            replace(p, kind=merged.kind, reason_code=merged.reason_code, risk_rank=3)
            for p in priors[client]
        ]
        priors[client] += [
            replace(p, risk_rank=2, evidence_ids=frozenset()) for p in priors[client]
        ]
    original = plan(candidates, contexts, priors, used, config, TODAY)
    survivors = [
        d
        for client, context in contexts.items()
        if (d := merge(candidates[client], context, config)) is not None
        and not is_suppressed(d, priors[client], config, TODAY)
    ]
    assert original.counts["items"] == len(survivors) == len(original.decisions)
    assert original.counts["suppressed"] == len(contexts) - len(survivors)
    assert original.counts["important"] <= max(0, config.budget_per_week - used)
    for d in original.decisions:
        assert d.client_id in contexts
        assert d.demoted_reason is None or (
            d.demoted_reason == "budget" and d.importance == "attention"
        )
        assert contexts[d.client_id].policy == "full" or not d.evidence_ids
    assert all(
        merge([c], contexts[client], config) is None
        for client in contexts
        for c in candidates[client]
        if classify(c, contexts[client], config) is None
    )
    for seed in range(6):
        rng = Random(seed)  # noqa: S311 - reproducible input permutations, not cryptography

        cs, xs, ps = list(candidates.items()), list(contexts.items()), list(priors.items())
        rng.shuffle(cs)
        rng.shuffle(xs)
        rng.shuffle(ps)
        for _, rows in cs:
            rng.shuffle(rows)
        for _, prior_rows in ps:
            rng.shuffle(prior_rows)
        assert plan(dict(cs), dict(xs), dict(ps), used, config, TODAY) == original


def test_budget_stable_equal_keys_and_no_loss() -> None:
    first = Decision(
        UUID(int=1), "state_change", "fixture", "important", None, (), frozenset(), 2, None, TODAY
    )
    second = replace(first, reason_code="other")
    result = apply_budget([first, second], load_config().budget_per_week - 1, load_config())
    assert result == (first, replace(second, importance="attention", demoted_reason="budget"))


def test_engine_has_no_clock_io_or_forbidden_imports() -> None:
    source = Path(engine.__file__).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(
                not {"db", "random", "logging"}.intersection(a.name.split(".")) for a in node.names
            )
        if isinstance(node, ast.ImportFrom):
            assert node.module is not None
            assert not any(part in node.module.split(".") for part in ("db", "random", "logging"))
            assert all(a.name not in ("now", "today") for a in node.names)
        if isinstance(node, ast.Call):
            assert not (isinstance(node.func, ast.Attribute) and node.func.attr in ("now", "today"))
            assert not (
                isinstance(node.func, ast.Name) and node.func.id in ("open", "print", "input")
            )
