"""Perfect-extraction weekly assessments, using the same loop as database replays."""

from collections.abc import Sequence
from datetime import date

from firra.assessment.config import load_assessment_config, load_plays_config
from firra.assessment.guard import volume_rule_types
from firra.assessment.model import Assessment
from firra.assessment.replay import replay_series
from firra.signals.config import SignalsConfig, load_config
from firra.signals.rules import RULES
from firra.synthetic.facts import client_facts, weekly_refs
from firra.synthetic.plan import Plan
from firra.synthetic.spec import Client, Spec


def weekly_assessments(
    spec: Spec,
    plan: Plan,
    client: Client,
    *,
    days: int = 180,
    steps: Sequence[date] | None = None,
) -> list[Assessment]:
    """Weekly steps ending at the client's last day, or the given ``steps`` (e.g. a DB replay's)."""
    base = load_config()
    signals = SignalsConfig(
        version=base.version,
        rules={name: {**params, "enabled": name in RULES} for name, params in base.rules.items()},
    )
    return replay_series(
        lambda ref: client_facts(spec, plan, client, ref),
        steps or [ref.date() for ref in weekly_refs(spec, client, days=days)],
        signals=signals,
        assessment=load_assessment_config(),
        plays=load_plays_config(),
        volume_types=volume_rule_types(),
    )
