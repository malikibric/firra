"""Ground truth of the synthetic dataset, derived from the spec and the plan (no model calls).

Committed as synthetic/northbeam/truth.json; a unit test fails when it drifts from the spec. The
evals (P2 step 7) compare the pipeline's output with it: expected items quote for quote, signals
per client, latent needs with exact counts, quotes that must never become items (injection), and
the account state each client must reach and by when (P2b: assessment, max_risk, caveats,
detect_by).
"""

import json
from pathlib import Path
from typing import Any

from firra.synthetic.plan import Plan
from firra.synthetic.spec import SPEC_PATH, Spec

TRUTH_PATH = SPEC_PATH.parent / "truth.json"


def ground_truth(spec: Spec, plan: Plan) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    forbidden: list[dict[str, str]] = []
    for thread in plan.threads:
        for message in thread.messages:
            for item in message.expect:
                fields = item.model_dump(mode="json", exclude_none=True)
                items.append(
                    {"external_id": message.external_id, "client": thread.client, **fields}
                )
            if thread.injection:
                forbidden.extend(
                    {"external_id": message.external_id, "quote": quote}
                    for quote in message.include
                )

    clients: dict[str, Any] = {}
    for client in spec.clients:
        scenarios = [s for s in spec.scenarios if s.client == client.key]
        clients[client.key] = {
            "name": client.name,
            "status": client.status,
            "policy": client.policy,
            "scenarios": [s.key for s in scenarios],
            "signals": sorted({t for s in scenarios for t in s.expect.signals}),
            "latent_needs": [
                n.model_dump(mode="json") for s in scenarios for n in s.expect.latent_needs
            ],
            "no_latent_needs": [t for s in scenarios for t in s.expect.no_latent_needs],
            "advise": next((s.expect.advise for s in scenarios if s.expect.advise), None),
            "suppressed_signals": sorted(
                {t for s in scenarios for t in s.expect.suppressed_signals}
            ),
            "assessment": next(
                (
                    s.expect.assessment.model_dump(mode="json")
                    for s in scenarios
                    if s.expect.assessment
                ),
                None,
            ),
            "max_risk": next((s.expect.max_risk for s in scenarios if s.expect.max_risk), None),
            "caveats": sorted({c for s in scenarios for c in s.expect.caveats}),
            "detect_by": next(
                (s.expect.detect_by.isoformat() for s in scenarios if s.expect.detect_by), None
            ),
        }

    return {
        "spec_version": spec.version,
        "as_of": spec.as_of.isoformat(),
        "counts": {
            "messages": plan.message_count,
            "planted_messages": sum(
                len(t.messages) for t in plan.threads if t.scenario is not None
            ),
            "threads": len(plan.threads),
            "meetings": len(plan.meetings),
            "expected_items": len(items),
        },
        "clients": clients,
        "items": items,
        "forbidden_quotes": forbidden,
    }


def render(truth: dict[str, Any]) -> str:
    return json.dumps(truth, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_truth(spec: Spec, plan: Plan, path: Path = TRUTH_PATH) -> None:
    path.write_text(render(ground_truth(spec, plan)))
