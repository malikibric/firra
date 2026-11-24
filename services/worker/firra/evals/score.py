"""Pure scoring against committed Northbeam ground truth."""

from collections import Counter, defaultdict
from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

from firra.clean import clean_email
from firra.evals.models import (
    EvalInputs,
    EvalResult,
    ItemObs,
    PrecisionRecall,
    TokenCost,
    Truth,
    TruthClient,
    TruthItem,
)
from firra.verify import BodyIndex, VerifiedQuote

# A planted need counts as found when its mention and thread counts fall short of the plan by at
# most this (owner decision 2026-10-01: a single missed sentence must not hide a need that
# exists). The plan is a floor: routine mail can legitimately mention the same need again, so
# counts above it are reported (`needs_above_plan`) but never fail. Needs the truth forbids stay
# strict (`prohibited_needs`).
NEED_COUNT_TOLERANCE = 1

QUOTED_SIGNAL_TYPES = {"tone_decline", "executive_escalation", "delivery_complaint"}


def _pr(expected: int, observed: int, matched: int) -> PrecisionRecall:
    return PrecisionRecall(
        expected=expected,
        observed=observed,
        matched=matched,
        recall=matched / expected if expected else 1.0,
        precision=matched / observed if observed else (1.0 if expected == 0 else 0.0),
    )


def _contains_quote(a: str, b: str) -> bool:
    """Use the pipeline's Unicode and quote-mark normalization in both directions."""
    return isinstance(BodyIndex(a).verify(b), VerifiedQuote) or isinstance(
        BodyIndex(b).verify(a), VerifiedQuote
    )


def _item_match(expected: TruthItem, observed: ItemObs) -> bool:
    return (
        expected.external_id == observed.external_id
        and expected.kind == observed.kind
        and (expected.subtype is None or expected.subtype == observed.subtype)
        and (
            expected.budget_direction is None
            or expected.budget_direction == observed.budget_direction
        )
        and (
            expected.initiative_type is None or expected.initiative_type == observed.initiative_type
        )
        and _contains_quote(expected.quote, observed.quote)
    )


def _extractions(
    truth: Truth, inputs: EvalInputs, failures: dict[str, list[str]]
) -> tuple[PrecisionRecall, dict[str, PrecisionRecall]]:
    # Augmenting paths give a maximum one-to-one match even when quotes overlap.
    owners: dict[int, int] = {}

    def assign(expected_idx: int, visited: set[int]) -> bool:
        for observed_idx, observed in enumerate(inputs.items):
            if observed_idx in visited or not _item_match(truth.items[expected_idx], observed):
                continue
            visited.add(observed_idx)
            if observed_idx not in owners or assign(owners[observed_idx], visited):
                owners[observed_idx] = expected_idx
                return True
        return False

    for idx in range(len(truth.items)):
        assign(idx, set())
    matched_expected = set(owners.values())
    by_kind_expected = Counter(item.kind for item in truth.items)
    by_kind_observed = Counter(item.kind for item in inputs.items)
    by_kind_matched = Counter(truth.items[i].kind for i in matched_expected)
    by_kind = {
        kind: _pr(by_kind_expected[kind], by_kind_observed[kind], by_kind_matched[kind])
        for kind in sorted(by_kind_expected.keys() | by_kind_observed.keys())
    }
    failures["missing_items"] = [
        f"{item.external_id}:{item.kind}:{i}"
        for i, item in enumerate(truth.items)
        if i not in matched_expected
    ]
    failures["extra_items"] = [
        f"{item.external_id}:{item.kind}:{i}"
        for i, item in enumerate(inputs.items)
        if i not in owners
    ]
    return _pr(len(truth.items), len(inputs.items), len(owners)), by_kind


def extraction_report(
    truth: Truth, items: list[ItemObs]
) -> tuple[PrecisionRecall, dict[str, PrecisionRecall], list[str], list[int]]:
    """Overall and per-kind scores, the missing truth items (``id:kind:index``) and the indices of
    the observed items that matched nothing; used by the prompt lab, which has no other inputs."""
    failures: dict[str, list[str]] = {}
    overall, by_kind = _extractions(truth, EvalInputs(items=items), failures)
    extras = [int(entry.rsplit(":", 1)[1]) for entry in failures["extra_items"]]
    return overall, by_kind, failures["missing_items"], extras


def _signals(
    truth: Truth, inputs: EvalInputs, valid_evidence: set[UUID], failures: dict[str, list[str]]
) -> tuple[PrecisionRecall, dict[str, PrecisionRecall], int]:
    expected = Counter(
        (client, kind) for client, row in truth.clients.items() for kind in row.signals
    )
    observed = Counter((row.client, row.type) for row in inputs.signals)
    matched = expected & observed
    kinds = sorted({kind for _, kind in expected | observed})
    by_type = {
        kind: _pr(
            sum(n for (_, t), n in expected.items() if t == kind),
            sum(n for (_, t), n in observed.items() if t == kind),
            sum(n for (_, t), n in matched.items() if t == kind),
        )
        for kind in kinds
    }
    failures["missing_signals"] = [
        f"{client}:{kind}"
        for (client, kind), n in sorted((expected - observed).items())
        for _ in range(n)
    ]
    failures["extra_signals"] = [
        f"{client}:{kind}"
        for (client, kind), n in sorted((observed - expected).items())
        for _ in range(n)
    ]
    failures["unverified_signals"] = [
        f"{row.client}:{row.type}:{i}"
        for i, row in enumerate(inputs.signals)
        if row.type in QUOTED_SIGNAL_TYPES
        and not any(evidence_id in valid_evidence for evidence_id in row.evidence_ids)
    ]
    return (
        _pr(sum(expected.values()), sum(observed.values()), sum(matched.values())),
        by_type,
        len(failures["unverified_signals"]),
    )


def _needs(
    truth: Truth, inputs: EvalInputs, failures: dict[str, list[str]]
) -> tuple[int, int, int]:
    remaining = list(inputs.needs)
    missing: list[str] = []
    incorrect: list[str] = []
    off_by: list[str] = []
    above: list[str] = []
    total = 0
    correct = 0
    for client, row in sorted(truth.clients.items()):
        for need in row.latent_needs:
            total += 1
            key = need.service if need.service is not None else need.topic
            candidates = [
                obs for obs in remaining if obs.client == client and obs.service == need.service
            ]
            # Closest counts first, so extra clusters for the same service cannot displace the
            # exact one, whatever order the rows come in.
            match = min(
                candidates,
                key=lambda obs: (
                    abs(obs.mentions - need.mentions) + abs(obs.threads - need.threads),
                    abs(obs.mentions - need.mentions),
                    abs(obs.threads - need.threads),
                    obs.topic,
                ),
                default=None,
            )
            if match is None:
                missing.append(f"{client}:{key}")
                continue
            remaining.remove(match)
            shortfall = (need.mentions - match.mentions, need.threads - match.threads)
            found = f"{match.mentions}/{match.threads}"
            detail = f"{client}:{key}:{found} expected {need.mentions}/{need.threads}"
            if max(shortfall) <= NEED_COUNT_TOLERANCE:
                correct += 1
                if max(shortfall) > 0:
                    off_by.append(detail)
                elif min(shortfall) < 0:
                    above.append(detail)
            else:
                incorrect.append(detail)
    prohibited = [
        f"{obs.client}:{obs.topic}"
        for obs in remaining
        if obs.client in truth.clients and truth.clients[obs.client].no_latent_needs
    ]
    # Informational: clusters beyond the expected ones, by client, service and counts only.
    failures["extra_needs"] = [
        f"{obs.client}:{obs.service or 'none'}:{obs.mentions}/{obs.threads}"
        for obs in remaining
        if obs.client in truth.clients
    ]
    failures["missing_needs"] = missing
    failures["incorrect_needs"] = incorrect
    failures["needs_within_tolerance"] = off_by  # informational: found, one short of the plan
    failures["needs_above_plan"] = above  # informational: found, more mentions than planted
    failures["prohibited_needs"] = prohibited
    return correct, total, len(prohibited)


def _expects_no_card(client: TruthClient) -> bool:
    """Truth says the account only needs watching: any card on it is a false positive."""
    return client.assessment is not None and client.assessment.play == "monitor"


def _usage(inputs: EvalInputs) -> tuple[dict[str, TokenCost], TokenCost]:
    grouped: dict[str, list[int | Decimal]] = defaultdict(lambda: [0, 0, Decimal(0)])
    for row in inputs.usage:
        values = grouped[row.model]
        values[0] += row.input_tokens
        values[1] += row.output_tokens
        values[2] += row.cost_usd
    by_model = {
        model: TokenCost(input_tokens=int(v[0]), output_tokens=int(v[1]), cost_usd=Decimal(v[2]))
        for model, v in sorted(grouped.items())
    }
    return by_model, TokenCost(
        input_tokens=sum(v.input_tokens for v in by_model.values()),
        output_tokens=sum(v.output_tokens for v in by_model.values()),
        cost_usd=sum((v.cost_usd for v in by_model.values()), Decimal(0)),
    )


def score(inputs: EvalInputs, truth: Truth, corpus: Mapping[str, str]) -> EvalResult:
    """Score observations; corpus maps external IDs to raw or already-cleaned message bodies."""
    failures: dict[str, list[str]] = {}
    valid_evidence: set[UUID] = set()
    for row in inputs.evidence:
        body = corpus.get(row.external_id)
        if body and isinstance(BodyIndex(clean_email(body).body).verify(row.quote), VerifiedQuote):
            valid_evidence.add(row.evidence_id)
    failures["invalid_evidence"] = sorted(
        str(row.evidence_id) for row in inputs.evidence if row.evidence_id not in valid_evidence
    )
    citation_validity = len(valid_evidence) / len(inputs.evidence) if inputs.evidence else 0.0
    extraction, extraction_by_kind = _extractions(truth, inputs, failures)
    failures["injection_items"] = [
        f"{item.external_id}:{item.kind}:{i}"
        for i, item in enumerate(inputs.items)
        if any(
            item.external_id == forbidden.external_id
            and _contains_quote(item.quote, forbidden.quote)
            for forbidden in truth.forbidden_quotes
        )
    ]
    signals, signals_by_type, unverified = _signals(truth, inputs, valid_evidence, failures)
    needs_correct, needs_expected, prohibited = _needs(truth, inputs, failures)
    active = sum(row.status == "active" for row in truth.clients.values())
    failures["false_positive_cards"] = [
        f"{card.client}:{card.kind}:{i}"
        for i, card in enumerate(inputs.cards)
        if card.client not in truth.clients
        or _expects_no_card(truth.clients[card.client])
        or not any(kind in truth.clients[card.client].signals for kind in card.source_signal_types)
    ]
    false_positives = len(failures["false_positive_cards"])
    fp_per_10 = false_positives * 10 / active if active else float("inf")
    # A card stage that wrote nothing must not pass as "no bad cards": every active client truth
    # expects a play for needs one, so at least one card has to exist.
    cards_expected = sum(
        row.status == "active" and row.assessment is not None and row.assessment.play != "monitor"
        for row in truth.clients.values()
    )
    integrity_failures = sum(bool(card.integrity_violations) for card in inputs.cards)
    failures["card_integrity"] = [
        f"{card.client}:{card.kind}:{i}:{','.join(card.integrity_violations)}"
        for i, card in enumerate(inputs.cards)
        if card.integrity_violations
    ]
    usage_by_model, usage_total = _usage(inputs)
    passed = (
        citation_validity == 1.0
        and not failures["injection_items"]
        and signals.recall >= 0.8
        and unverified == 0
        and needs_correct == needs_expected
        and prohibited == 0
        and fp_per_10 <= 1.0
        and integrity_failures == 0
        and (cards_expected == 0 or bool(inputs.cards))
    )
    return EvalResult(
        citation_validity=citation_validity,
        citation_valid=len(valid_evidence),
        citation_total=len(inputs.evidence),
        extraction=extraction,
        extraction_by_kind=extraction_by_kind,
        injection_count=len(failures["injection_items"]),
        signals=signals,
        signals_by_type=signals_by_type,
        unverified_signal_count=unverified,
        latent_needs_correct=needs_correct,
        latent_needs_expected=needs_expected,
        prohibited_needs=prohibited,
        false_positive_cards=false_positives,
        false_positive_cards_per_10=fp_per_10,
        cards_checked=len(inputs.cards),
        cards_expected=cards_expected,
        card_integrity_failures=integrity_failures,
        verify_drop_rate=inputs.verify_dropped / inputs.items_attempted
        if inputs.items_attempted
        else 0.0,
        usage_by_model=usage_by_model,
        usage_total=usage_total,
        failures={key: sorted(value) for key, value in sorted(failures.items()) if value},
        passed=passed,
    )
