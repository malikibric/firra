"""Runs every case of oracle.yaml (T43c, written from docs/plans/e-routines-engine.md) through the
pure routine engine.

Expectations are never edited to make a function pass; a disagreement goes to the lead and either
the spec or the code changes.
"""

# ruff: noqa: S311

import random
from datetime import date
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5

import pytest
import yaml  # type: ignore[import-untyped]

from firra.assessment.config import ExpansionState, RiskState
from firra.routines.config import RoutinesConfig, load_config
from firra.routines.engine import apply_budget, classify, is_suppressed, merge, plan
from firra.routines.model import Candidate, ClientContext, Decision, Kind, Prior, Trigger

NAMESPACE = UUID("6f1c2d3e-0000-4000-8000-000000000001")
ORACLE: dict[str, list[dict[str, Any]]] = yaml.safe_load(
    (Path(__file__).parent / "oracle.yaml").read_text()
)

# The cases run against the shipping config; `test_shipping_config_matches_the_spec` pins its
# numbers to the spec so a drifted `config/routines.yaml` fails the oracle.
CONFIG: RoutinesConfig = load_config()

Verdict = Literal["useful", "already_known", "wrong", "not_now"]
Policy = Literal["full", "metadata_only"]
RISKS: list[RiskState] = ["stable", "watch", "elevated", "high"]
TRIGGERS: list[Trigger] = [
    "risk_up",
    "risk_down",
    "expansion_up",
    "signal_opened",
    "signal_resolved",
    "commitment_late",
    "notice_close",
]
KINDS: list[Kind] = ["state_change", "commitment_watch", "expansion"]
POLICIES: list[Policy] = ["full", "metadata_only"]
VERDICTS: list[Verdict | None] = ["useful", "already_known", "wrong", "not_now", None]
EXPANSIONS: list[ExpansionState] = ["none", "hypothesis", "strong"]


def uid(kind: str, label: str) -> UUID:
    return uuid5(NAMESPACE, f"{kind}:{label}")


def client_uid(label: str) -> UUID:
    # Ordered, deterministic ids so the spec's client-id tie-break is hand-derivable
    # (c1 < c2 < … as strings). Everything else uses uuid5.
    suffix = label[1:] if label.startswith("c") and label[1:].isdigit() else ""
    if suffix:
        return UUID(f"00000000-0000-4000-8000-{int(suffix):012d}")
    return uuid5(NAMESPACE, f"client:{label}")


def on(value: str | date) -> date:
    # PyYAML parses an ISO `YYYY-MM-DD` into a `date`; accept both so the schema stays readable.
    return value if isinstance(value, date) else date.fromisoformat(value)


def evidence(labels: list[str]) -> frozenset[UUID]:
    return frozenset(uid("evidence", label) for label in labels)


def candidate(raw: dict[str, Any], client: str = "c1") -> Candidate:
    return Candidate(
        client_id=client_uid(raw.get("client", client)),
        kind=raw["kind"],
        trigger=raw["trigger"],
        as_of=on(raw["as_of"]),
        risk_to=raw.get("risk_to"),
        expansion_to=raw.get("expansion_to"),
        severity=raw.get("severity"),
        owner_side=raw.get("owner_side"),
        evidence_ids=evidence(raw.get("evidence", [])),
    )


def context(raw: dict[str, Any], client: str = "c1") -> ClientContext:
    return ClientContext(
        client_id=client_uid(client),
        policy=raw.get("policy", "full"),
        risk=raw["risk"],
        days_to_notice=raw.get("days_to_notice"),
    )


def decision(raw: dict[str, Any]) -> Decision:
    return Decision(
        client_id=client_uid(raw["client"]),
        kind=raw["kind"],
        reason_code=raw["reason_code"],
        importance=raw["importance"],
        demoted_reason=raw.get("demoted_reason"),
        also=tuple(raw.get("also", [])),
        evidence_ids=evidence(raw.get("evidence", [])),
        risk_rank=raw["risk_rank"],
        days_to_notice=raw.get("days_to_notice"),
        as_of=on(raw["as_of"]),
    )


def prior(raw: dict[str, Any]) -> Prior:
    return Prior(
        kind=raw["kind"],
        reason_code=raw["reason_code"],
        verdict=raw.get("verdict"),
        surfaced_on=on(raw["surfaced_on"]),
        evidence_ids=evidence(raw.get("evidence", [])),
        risk_rank=raw["risk_rank"],
    )


def check_decision(got: Decision, want: dict[str, Any]) -> None:
    assert str(got.client_id) == str(client_uid(want.get("client", "c1")))
    assert got.kind == want["kind"]
    assert got.reason_code == want["reason_code"]
    assert got.importance == want["importance"]
    assert got.demoted_reason == want.get("demoted_reason")
    assert tuple(got.also) == tuple(want.get("also", []))
    assert got.evidence_ids == evidence(want.get("evidence", []))
    assert got.risk_rank == want["risk_rank"]
    assert got.days_to_notice == want.get("days_to_notice")
    assert got.as_of == on(want["as_of"])


def cases(group: str) -> list[Any]:
    return [pytest.param(case, id=case["id"]) for case in ORACLE[group]]


def test_shipping_config_matches_the_spec() -> None:
    config = load_config()
    assert config.budget_per_week == 3
    assert config.wrong_hold_days == 30
    assert config.notice_close_days == 14
    assert config.notice_near_days == 30


@pytest.mark.parametrize("case", cases("classify"))
def test_classify(case: dict[str, Any]) -> None:
    raw = case["input"]
    cand = candidate(
        {
            "kind": "state_change",
            "trigger": raw["trigger"],
            "as_of": "2026-10-01",
            **{
                key: raw[key]
                for key in ("risk_to", "expansion_to", "severity", "owner_side")
                if key in raw
            },
        }
    )
    result = classify(cand, context(raw), CONFIG)
    want = case["expect"]
    if want is None:
        assert result is None
    else:
        assert result == (want["importance"], want["reason_code"])


@pytest.mark.parametrize("case", cases("merge"))
def test_merge(case: dict[str, Any]) -> None:
    raw = case["input"]
    client = raw.get("client", "c1")
    cands = [candidate(c, client) for c in raw["candidates"]]
    result = merge(cands, context(raw["context"], client), CONFIG)
    want = case["expect"]
    if want is None:
        assert result is None
    else:
        assert result is not None
        check_decision(result, want["decision"])


@pytest.mark.parametrize("case", cases("is_suppressed"))
def test_is_suppressed(case: dict[str, Any]) -> None:
    raw = case["input"]
    dec = decision({**raw["decision"], "client": "c1", "importance": "fyi", "as_of": "2026-10-01"})
    priors = [prior(p) for p in raw["priors"]]
    assert is_suppressed(dec, priors, CONFIG, on(raw["today"])) is case["expect"]


@pytest.mark.parametrize("case", cases("apply_budget"))
def test_apply_budget(case: dict[str, Any]) -> None:
    raw = case["input"]
    ordered = apply_budget([decision(d) for d in raw["decisions"]], raw["important_used"], CONFIG)
    got = [(str(d.client_id), d.reason_code, d.importance, d.demoted_reason) for d in ordered]
    want = [
        (
            str(client_uid(w["client"])),
            w["reason_code"],
            w["importance"],
            w.get("demoted_reason"),
        )
        for w in case["expect"]
    ]
    assert got == want


def build_plan_inputs(
    raw: dict[str, Any],
) -> tuple[dict[UUID, list[Candidate]], dict[UUID, ClientContext], dict[UUID, list[Prior]]]:
    candidates: dict[UUID, list[Candidate]] = {}
    contexts: dict[UUID, ClientContext] = {}
    priors: dict[UUID, list[Prior]] = {}
    for client in raw["clients"]:
        cid = client_uid(client["client"])
        if "candidates" in client:
            candidates[cid] = [candidate(c, client["client"]) for c in client["candidates"]]
        if "context" in client:
            contexts[cid] = context(client["context"], client["client"])
        if "priors" in client:
            priors[cid] = [prior(p) for p in client["priors"]]
    return candidates, contexts, priors


@pytest.mark.parametrize("case", cases("plan"))
def test_plan(case: dict[str, Any]) -> None:
    raw = case["input"]
    candidates, contexts, priors = build_plan_inputs(raw)
    result = plan(candidates, contexts, priors, raw["important_used"], CONFIG, on(raw["today"]))
    assert result.counts == case["expect"]["counts"]
    assert len(result.decisions) == len(case["expect"]["decisions"])
    for got, want in zip(result.decisions, case["expect"]["decisions"], strict=True):
        check_decision(got, want)


def test_every_function_group_has_cases() -> None:
    assert set(ORACLE) == {"classify", "merge", "is_suppressed", "apply_budget", "plan"}


def test_oracle_cases_are_well_formed() -> None:
    for group, group_cases in ORACLE.items():
        assert group_cases, group
        for case in group_cases:
            assert set(case) == {"id", "why", "input", "expect"}, case.get("id")
            assert isinstance(case["id"], str) and case["id"]
            assert isinstance(case["why"], str) and case["why"]


# --------------------------------------------------------------------------------------------------
# Invariants 1-6 over generated inputs (fixed seed, at least 30 workspaces).
# --------------------------------------------------------------------------------------------------

SEED = 20261001
PROPERTY_RUNS = 40


def random_candidate(rng: random.Random, cid: UUID) -> Candidate:
    trigger = rng.choice(TRIGGERS)
    extra: dict[str, Any] = {}
    if trigger in {"risk_up", "risk_down"}:
        extra["risk_to"] = rng.choice(RISKS)
    elif trigger == "expansion_up":
        extra["expansion_to"] = rng.choice(EXPANSIONS)
    elif trigger == "signal_opened":
        extra["severity"] = rng.choice(["low", "medium", "high"])
    elif trigger == "commitment_late":
        extra["owner_side"] = rng.choice(["firm", "client", None])
    ids = frozenset(uid("evidence", f"e{rng.randint(1, 4)}") for _ in range(rng.randint(0, 3)))
    return Candidate(
        client_id=cid,
        kind=rng.choice(KINDS),
        trigger=trigger,
        as_of=date(2026, 10, 1),
        evidence_ids=ids,
        **extra,
    )


def random_workspace(
    rng: random.Random,
) -> tuple[dict[UUID, list[Candidate]], dict[UUID, ClientContext], dict[UUID, list[Prior]]]:
    candidates: dict[UUID, list[Candidate]] = {}
    contexts: dict[UUID, ClientContext] = {}
    priors: dict[UUID, list[Prior]] = {}
    for index in range(rng.randint(1, 6)):
        cid = client_uid(f"c{index}")
        cand = random_candidate(rng, cid)
        candidates[cid] = [cand]
        if rng.random() < 0.8:
            contexts[cid] = ClientContext(
                client_id=cid,
                policy=rng.choice(POLICIES),
                risk=rng.choice(RISKS),
                days_to_notice=rng.choice([None, -1, 0, 5, 14, 15, 30, 31]),
            )
        if rng.random() < 0.4:
            priors[cid] = [
                Prior(
                    kind=cand.kind,
                    reason_code=f"reason_{rng.randint(1, 3)}",
                    verdict=rng.choice(VERDICTS),
                    surfaced_on=date(2026, 9, rng.randint(1, 28)),
                    evidence_ids=cand.evidence_ids,
                    risk_rank=rng.randint(0, 3),
                )
            ]
    return candidates, contexts, priors


def test_plan_invariants() -> None:
    rng = random.Random(SEED)
    for _ in range(PROPERTY_RUNS):
        candidates, contexts, priors = random_workspace(rng)
        used = rng.choice([0, 1, 2, 3, 5])
        result = plan(candidates, contexts, priors, used, CONFIG, date(2026, 10, 1))

        # 1. Deterministic under any dict order.
        shuffled = dict(reversed(list(candidates.items())))
        assert result == plan(shuffled, contexts, priors, used, CONFIG, date(2026, 10, 1))

        # 2. Never more important decisions than free slots.
        free = max(0, 3 - used)
        assert sum(d.importance == "important" for d in result.decisions) <= free

        # 3. Only a demoted decision carries `budget`, and it is attention.
        for d in result.decisions:
            if d.demoted_reason is not None:
                assert d.demoted_reason == "budget"
                assert d.importance == "attention"

        # 4. Decisions only for contexts whose candidates keep something after classify.
        for d in result.decisions:
            assert d.client_id in contexts
            assert any(
                classify(c, contexts[d.client_id], CONFIG) for c in candidates.get(d.client_id, [])
            )

        # 5. A metadata_only client never carries evidence.
        for d in result.decisions:
            if contexts[d.client_id].policy == "metadata_only":
                assert d.evidence_ids == frozenset()

        # 6. Nothing is dropped silently by the budget.
        expected_items = 0
        expected_suppressed = 0
        for cid, ctx in contexts.items():
            merged = merge(candidates.get(cid, []), ctx, CONFIG)
            if merged is None:
                continue
            if is_suppressed(merged, priors.get(cid, []), CONFIG, date(2026, 10, 1)):
                expected_suppressed += 1
            else:
                expected_items += 1
        assert result.counts["items"] == len(result.decisions)
        assert result.counts["items"] == expected_items
        assert result.counts["suppressed"] == expected_suppressed
