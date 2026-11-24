"""The Northbeam spec and the signal rules agree (p2b D12), with no model and no database.

Facts come from the plan as if ingest and extraction were perfect (firra/synthetic/facts.py). For an
active client the rules run at the dataset's as-of date; for a churned client at every week of the
180 days before its churn date, and the union counts. Expected = `signals` plus
`suppressed_signals` (they fire on the raw data; the coverage guard hides them later). Types whose
rule is not implemented yet are left out on both sides, so the test tightens as rules land.
"""

import pytest

from firra.signals.config import SignalsConfig, load_config
from firra.signals.engine import evaluate
from firra.signals.facts import ClientFacts
from firra.signals.rules import RULES
from firra.synthetic.facts import client_facts, end_of, weekly_refs
from firra.synthetic.plan import build_plan
from firra.synthetic.spec import Client, load_spec

SPEC = load_spec()
PLAN = build_plan(SPEC)
_BASE = load_config()
CONFIG = SignalsConfig(
    version=_BASE.version,
    rules={name: {**params, "enabled": name in RULES} for name, params in _BASE.rules.items()},
)


def _types(facts: ClientFacts) -> set[str]:
    return {d.type for d in evaluate(facts, CONFIG, RULES).drafts}


def _produced(client: Client) -> set[str]:
    if client.status == "churned":
        return set().union(
            *(_types(client_facts(SPEC, PLAN, client, ref)) for ref in weekly_refs(SPEC, client))
        )
    return _types(client_facts(SPEC, PLAN, client, end_of(SPEC.as_of)))


@pytest.mark.parametrize("client", SPEC.clients, ids=lambda c: c.key)
def test_rules_fire_exactly_the_expected_signals(client: Client) -> None:
    expects = [s.expect for s in SPEC.scenarios if s.client == client.key]
    wanted = {t for e in expects for t in (*e.signals, *e.suppressed_signals)} & set(RULES)
    produced = _produced(client)
    assert produced == wanted
