"""Stable machine and human-readable evaluation artifacts."""

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from firra.evals.models import EvalResult, ReplayEvalResult
from firra.evals.replay_score import ACCURACY_MIN, FALSE_ALARM_MAX


def write_report(result: EvalResult, out_dir: Path, replay: ReplayEvalResult | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = result.model_dump(mode="json")
    if replay is not None:
        payload["replay"] = replay.model_dump(mode="json")
        # Top level is the overall verdict, like eval.md and the exit code.
        payload["base_passed"] = result.passed
        payload["passed"] = result.passed and replay.passed
    (out_dir / "eval.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    rows: list[tuple[str, str, str, str]] = []

    def add(
        name: str, value: str | int | float | Decimal, threshold: str = "—", ok: bool | None = None
    ) -> None:
        status = "—" if ok is None else ("yes" if ok else "no")
        rows.append((name, str(value), threshold, status))

    add(
        "Citation validity", f"{result.citation_validity:.3f}", "1.0", result.citation_validity == 1
    )
    add("Extraction recall", f"{result.extraction.recall:.3f}")
    add("Extraction precision", f"{result.extraction.precision:.3f}")
    for kind, value in sorted(result.extraction_by_kind.items()):
        add(f"Extraction {kind} recall", f"{value.recall:.3f}")
        add(f"Extraction {kind} precision", f"{value.precision:.3f}")
    add("Injection items", result.injection_count, "0", result.injection_count == 0)
    add("Signal recall", f"{result.signals.recall:.3f}", "≥ 0.8", result.signals.recall >= 0.8)
    add("Signal precision", f"{result.signals.precision:.3f}")
    for kind, value in sorted(result.signals_by_type.items()):
        add(f"Signal {kind} recall", f"{value.recall:.3f}")
        add(f"Signal {kind} precision", f"{value.precision:.3f}")
    add(
        "Signals without verified quote",
        result.unverified_signal_count,
        "0",
        result.unverified_signal_count == 0,
    )
    add(
        "Latent needs found (counts within ±1)",
        f"{result.latent_needs_correct}/{result.latent_needs_expected}",
        "all",
        result.latent_needs_correct == result.latent_needs_expected,
    )
    add("Prohibited latent needs", result.prohibited_needs, "0", result.prohibited_needs == 0)
    add(
        "False-positive cards per 10 active clients",
        f"{result.false_positive_cards_per_10:.3f}",
        "≤ 1",
        result.false_positive_cards_per_10 <= 1,
    )
    add(
        "Cards written",
        result.cards_checked,
        "≥ 1" if result.cards_expected else "—",
        result.cards_expected == 0 or result.cards_checked > 0,
    )
    add(
        "Card numeric-integrity failures",
        result.card_integrity_failures,
        "0",
        result.card_integrity_failures == 0,
    )
    add("Verify drop rate", f"{result.verify_drop_rate:.3f}")
    if replay is not None:
        _add_replay_rows(add, replay)
    for model, usage in sorted(result.usage_by_model.items()):
        add(f"{model} input tokens", usage.input_tokens)
        add(f"{model} output tokens", usage.output_tokens)
        add(f"{model} cost USD", usage.cost_usd)
    add("Total input tokens", result.usage_total.input_tokens)
    add("Total output tokens", result.usage_total.output_tokens)
    add("Total cost USD", result.usage_total.cost_usd)

    lines = [
        "# Northbeam evaluation",
        "",
        f"Overall: **{'PASS' if result.passed and (replay is None or replay.passed) else 'FAIL'}**",
        "",
        "| Metric | Value | Threshold | Pass |",
        "|---|---:|---:|:---:|",
        *(
            f"| {name} | {value} | {threshold} | {status} |"
            for name, value, threshold, status in rows
        ),
        "",
        "## Failures by ID",
        "",
    ]
    failures = {**result.failures}
    if replay is not None:
        failures.update({f"replay: {group}": ids for group, ids in replay.failures.items()})
    if not failures:
        lines.append("None.")
    for group, ids in sorted(failures.items()):
        lines.extend([f"### {group}", "", *(f"- `{value}`" for value in sorted(ids)), ""])
    (out_dir / "eval.md").write_text("\n".join(lines).rstrip() + "\n")


def _ratio(correct: int, checked: int) -> str:
    return f"{correct}/{checked}"


def _add_replay_rows(
    add: Callable[..., None],
    replay: ReplayEvalResult,
) -> None:
    for name, correct, checked in (
        ("Replay risk state at as_of", replay.risk_correct, replay.risk_checked),
        ("Replay expansion state at as_of", replay.expansion_correct, replay.expansion_checked),
        ("Replay play at as_of", replay.play_correct, replay.play_checked),
    ):
        ok = checked == 0 or correct / checked >= ACCURACY_MIN
        add(name, _ratio(correct, checked), f"≥ {ACCURACY_MIN}", ok)
    for key, met in sorted(replay.by_met.items()):
        add(f"Replay `by` met: {key}", "yes" if met else "no", "yes", met)
    add(
        "Churned detected in time",
        _ratio(replay.churned_detected, replay.churned_checked),
        "≥ 2 of 3",
        replay.churned_detected * 3 >= replay.churned_checked * 2,
    )
    add(
        "Benign controls over their ceiling",
        len(replay.max_risk_exceeded),
        "0",
        not replay.max_risk_exceeded,
    )
    add(
        "False-alarm client-weeks per 10 clients per month",
        f"{replay.false_alarm_weeks_per_10_per_month:.3f}",
        f"≤ {FALSE_ALARM_MAX:g}",
        replay.false_alarm_weeks_per_10_per_month <= FALSE_ALARM_MAX,
    )
    for key, days in sorted(replay.lead_days.items()):
        add(f"Lead days before notice or churn: {key}", "none" if days is None else days)
    if replay.median_lead_days is not None:
        add("Median lead days (losses)", f"{replay.median_lead_days:g}")
    add("Replay flicker", replay.flicker, "0", replay.flicker == 0)
    for key, missing in sorted(replay.caveats_missing.items()):
        add(f"Missing caveats: {key}", ", ".join(missing))
