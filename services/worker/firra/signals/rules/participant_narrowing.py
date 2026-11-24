"""Compare distinct client authors with the mean of earlier equal windows."""

from statistics import mean

from pydantic import Field

from firra.signals.facts import ClientFacts
from firra.signals.model import Severity, SignalDraft
from firra.signals.rules.base import RuleConfig, RuleSpec, in_window


class ParticipantNarrowingConfig(RuleConfig):
    window_days: int = Field(gt=0)
    baseline_windows: int = Field(gt=0)
    min_baseline_authors: float = Field(gt=0)
    max_ratio: float = Field(gt=0)
    watch_ratio: float = Field(gt=0)
    severity: Severity
    watch_severity: Severity
    dedupe_key: str


def evaluate(facts: ClientFacts, cfg: ParticipantNarrowingConfig) -> list[SignalDraft]:
    messages = facts.client_messages()

    def authors(start: int, end: int) -> int:
        return len(
            {
                r.author_person_id
                for r in messages
                if r.author_person_id is not None
                and in_window(r.occurred_at, facts.ref, newer_than_days=end, up_to_days=start)
            }
        )

    recent = authors(0, cfg.window_days)
    baseline = mean(
        authors(i * cfg.window_days, (i + 1) * cfg.window_days)
        for i in range(1, cfg.baseline_windows + 1)
    )
    if baseline < cfg.min_baseline_authors or recent > cfg.watch_ratio * baseline:
        return []
    return [
        SignalDraft(
            type="participant_narrowing",
            family=cfg.family,
            severity=cfg.severity if recent <= cfg.max_ratio * baseline else cfg.watch_severity,
            occurred_at=facts.ref,
            basis="metadata",
            dedupe_key=cfg.dedupe_key,
            inputs={"authors_60": recent, "baseline_authors_per_60": round(baseline, 2)},
        )
    ]


SPEC = RuleSpec(ParticipantNarrowingConfig, evaluate, uses_volume=True)
