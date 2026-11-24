"""Churn Replay narrative (roadmap milestone B): what changed, when, and why, from stored rows.

A pure function over one client's replay rows. Every sentence comes from the drivers' labels and
inputs (`firra/cards/facts.py`); nothing is written by a model, so every claim is a stored fact
with evidence ids. Quotes are attached by the report renderer from verified evidence.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import date
from typing import Any
from uuid import UUID

from firra.assessment.config import RiskState, risk_rank
from firra.assessment.summary import Step, detection
from firra.cards.facts import LABELS, inputs_text

RISK_FAMILIES = {"delivery", "sentiment", "stakeholder", "commercial", "meetings", "engagement"}


@dataclass(frozen=True, slots=True)
class DriverView:
    type: str
    family: str | None
    severity: str
    label: str
    detail: str
    occurred_at: date | None
    evidence_ids: tuple[UUID, ...]
    primary: bool

    basis: str = "metadata"
    inputs: dict[str, Any] | None = field(default=None, hash=False)

    @property
    def sentence(self) -> str:
        return self.label + (f" ({self.detail})" if self.detail else "")


@dataclass(frozen=True, slots=True)
class StepRow:
    """One stored replay assessment, reduced to what the narrative needs."""

    as_of: date
    risk: RiskState
    expansion: str
    play: str
    strength: str
    drivers: tuple[dict[str, Any], ...]
    caveats: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Transition:
    as_of: date
    from_state: RiskState | None
    to_state: RiskState
    play: str
    strength: str
    new_drivers: tuple[DriverView, ...]
    caveats: tuple[str, ...]
    days_before_end: int | None
    active_drivers: tuple[DriverView, ...] = ()
    only_ended: bool = False


@dataclass(frozen=True, slots=True)
class Event:
    as_of: date
    kind: str
    driver: DriverView | None = None
    old_severity: str | None = None
    grouping_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class Narrative:
    client_name: str
    status: str
    end_date: date | None
    end_kind: str | None  # "notice" | "churn"
    detection_at: date | None
    detection_state: RiskState | None
    confirmed_at: date | None  # the following weekly assessment that confirmed the first alert
    lead_days: int | None
    confirmed_lead_days: int | None
    max_state: RiskState | None
    weeks_elevated: int
    transitions: tuple[Transition, ...]
    visible_at_detection: tuple[DriverView, ...]
    detection_play: str | None
    caveats: tuple[str, ...]
    last_as_of: date | None
    last_state: RiskState | None
    events: tuple[Event, ...]
    detection_timing: tuple[DriverView, ...]
    unconfirmed_at_end: bool  # the final assessment is elevated/high and no later week confirms it


def _view(raw: dict[str, Any]) -> DriverView:
    occurred = raw.get("occurred_at")
    return DriverView(
        type=raw["type"],
        family=raw.get("family"),
        severity=raw["severity"],
        label=LABELS.get(raw["type"], str(raw["type"]).replace("_", " ").capitalize()),
        detail=inputs_text(raw.get("inputs"), raw["type"])
        if raw.get("basis") == "metadata"
        else "",
        occurred_at=date.fromisoformat(occurred[:10]) if occurred else None,
        evidence_ids=tuple(UUID(e) for e in raw.get("evidence_ids", [])),
        primary=bool(raw.get("primary")),
        basis=raw.get("basis", "metadata"),
        inputs=raw.get("inputs") if raw.get("basis") == "metadata" else None,
    )


def _key(raw: dict[str, Any]) -> tuple[str, str]:
    return (raw["type"], raw.get("dedupe_key", ""))


def _peak(rows: Sequence[StepRow]) -> RiskState | None:
    if not rows:
        return None
    best: RiskState = max((r.risk for r in rows), key=risk_rank)
    return best


def build_narrative(
    rows: Sequence[StepRow],
    *,
    client_name: str,
    status: str,
    churned_at: date | None = None,
    notice_at: date | None = None,
    first_known_at: date | None = None,
) -> Narrative:
    ordered = sorted(rows, key=lambda r: r.as_of)
    end = (notice_at or churned_at) if status == "churned" else None
    end_kind = ("notice" if notice_at else "churn") if end else None
    detected = detection([Step(r.as_of, r.risk) for r in ordered])
    transitions: list[Transition] = []
    events = [
        Event(at, kind)
        for at, kind in (
            (notice_at, "notice"),
            (churned_at, "churn"),
            (first_known_at, "first_known"),
        )
        if at is not None
    ]

    def relevant(row: StepRow | None) -> dict[tuple[str, str], dict[str, Any]]:
        return (
            {
                _key(d): d
                for d in row.drivers
                if d["type"] in LABELS and d.get("family") in RISK_FAMILIES | {"timing"}
            }
            if row
            else {}
        )

    previous: StepRow | None = None
    for row in ordered:
        before = previous.risk if previous else None
        old, current = relevant(previous), relevant(row)
        ended = old.keys() - current.keys()
        for key, raw in current.items():
            if key not in old:
                if previous and row.risk == before:
                    events.append(Event(row.as_of, "new_driver", _view(raw)))
                continue
            added = tuple(
                e for e in _view(raw).evidence_ids if e not in _view(old[key]).evidence_ids
            )
            if added:
                events.append(
                    Event(
                        row.as_of,
                        "new_evidence",
                        replace(_view(raw), evidence_ids=added),
                        grouping_ids=_view(raw).evidence_ids,
                    )
                )
            if raw["severity"] != old[key]["severity"]:
                events.append(
                    Event(row.as_of, "severity_changed", _view(raw), old[key]["severity"])
                )
        for key in sorted(ended):
            events.append(Event(row.as_of, "ended", _view(old[key])))
        if row.risk != before and (before is not None or row.risk != "stable"):
            seen = {_key(d) for d in previous.drivers} if previous else set()
            new = tuple(_view(d) for key, d in current.items() if key not in seen)
            transitions.append(
                Transition(
                    as_of=row.as_of,
                    from_state=before,
                    to_state=row.risk,
                    play=row.play,
                    strength=row.strength,
                    new_drivers=new,
                    active_drivers=tuple(
                        sorted((_view(d) for d in current.values()), key=lambda d: not d.primary)
                    ),
                    only_ended=bool(ended)
                    and before is not None
                    and risk_rank(row.risk) < risk_rank(before)
                    and all(old.get(k) == d for k, d in current.items()),
                    caveats=row.caveats,
                    days_before_end=(end - row.as_of).days if end else None,
                )
            )
        previous = row
    at_detection = next((r for r in ordered if r.as_of == detected), None)
    confirmed = next((r.as_of for r in ordered if detected and r.as_of > detected), None)
    last = ordered[-1] if ordered else None
    unconfirmed = bool(
        last
        and risk_rank(last.risk) >= risk_rank("elevated")
        and (len(ordered) == 1 or risk_rank(ordered[-2].risk) < risk_rank("elevated"))
    )
    visible = (
        tuple(_view(d) for d in at_detection.drivers if d.get("family") in RISK_FAMILIES)
        if at_detection
        else ()
    )
    return Narrative(
        client_name=client_name,
        status=status,
        end_date=end,
        end_kind=end_kind,
        detection_at=detected,
        detection_state=at_detection.risk if at_detection else None,
        confirmed_at=confirmed,
        lead_days=(end - detected).days if end and detected else None,
        confirmed_lead_days=(end - confirmed).days if end and confirmed else None,
        max_state=_peak(ordered),
        weeks_elevated=sum(risk_rank(r.risk) >= risk_rank("elevated") for r in ordered),
        transitions=tuple(transitions),
        events=tuple(sorted(events, key=lambda e: e.as_of)),
        detection_timing=tuple(
            _view(d) for d in relevant(at_detection).values() if d.get("family") == "timing"
        ),
        visible_at_detection=visible,
        detection_play=at_detection.play if at_detection else None,
        caveats=last.caveats if last else (),
        last_as_of=last.as_of if last else None,
        last_state=last.risk if last else None,
        unconfirmed_at_end=unconfirmed,
    )


def narrative_to_json(narrative: Narrative) -> dict[str, Any]:
    """Canonical storage excludes client names and rendered wording."""

    def encode(value: Any) -> Any:
        if isinstance(value, (date, UUID)):
            return value.isoformat() if isinstance(value, date) else str(value)
        if isinstance(value, (tuple, list)):
            return [encode(v) for v in value]
        if isinstance(value, dict):
            return {k: encode(v) for k, v in value.items()}
        if isinstance(value, (Narrative, Transition, Event, DriverView)):
            result = {
                f.name: encode(getattr(value, f.name))
                for f in fields(value)
                if f.name not in {"client_name", "label", "detail", "inputs"}
            }
            if isinstance(value, DriverView) and value.basis == "metadata":
                result["inputs"] = metadata_inputs(value.inputs)
                result["evidence_ids"] = []
            return result
        return value

    result: dict[str, Any] = encode(narrative)
    return result


_NUMERIC_INPUTS = {
    "affected",
    "total",
    "share",
    "initiated_30",
    "baseline_per_30",
    "authors_60",
    "baseline_authors_per_60",
    "messages_30",
    "recent_median_h",
    "base_median_h",
    "recent_n",
    "base_n",
    "mean_90",
    "base_mean",
    "messages_31_180",
    "days_to_notice",
    "days",
    "overdue_days",
}
_DATE_INPUTS = {
    "last_message_at",
    "renewal_date",
    "notice_deadline",
    "contract_start",
    "departed_at",
    "first_seen_at",
    "due_date",
}


def metadata_inputs(inputs: dict[str, Any] | None) -> dict[str, Any]:
    from datetime import datetime

    result: dict[str, Any] = {}
    for key, value in (inputs or {}).items():
        if key in _NUMERIC_INPUTS and (
            value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))
        ):
            result[key] = value
        elif key in _DATE_INPUTS:
            if value is not None:
                datetime.fromisoformat(str(value))
            result[key] = value.isoformat() if isinstance(value, date) else value
        elif key == "person_id":
            result[key] = str(UUID(str(value)))
        # Anything else is dropped, not stored: the typed lists above are the privacy boundary
        # (a new rule adds its keys there) and an unknown key must not fail a whole replay run.
    return result


def narrative_from_json(raw: dict[str, Any], *, client_name: str = "") -> Narrative:
    """Rebuild deterministic report wording from typed stored inputs."""

    def drivers(values: list[dict[str, Any]]) -> tuple[DriverView, ...]:
        return tuple(_view(v) for v in values)

    def dated(values: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        return {**values, **{k: date.fromisoformat(values[k]) if values[k] else None for k in keys}}

    transitions = tuple(
        Transition(
            **{
                **dated(t, ("as_of",)),
                "new_drivers": drivers(t["new_drivers"]),
                "active_drivers": drivers(t["active_drivers"]),
                "caveats": tuple(t["caveats"]),
            }
        )
        for t in raw["transitions"]
    )
    events = tuple(
        Event(
            **{
                **dated(e, ("as_of",)),
                "driver": _view(e["driver"]) if e["driver"] else None,
                "grouping_ids": tuple(UUID(i) for i in e["grouping_ids"]),
            }
        )
        for e in raw["events"]
    )
    return Narrative(
        **{
            **dated(raw, ("end_date", "detection_at", "confirmed_at", "last_as_of")),
            "client_name": client_name,
            "transitions": transitions,
            "events": events,
            "visible_at_detection": drivers(raw["visible_at_detection"]),
            "detection_timing": drivers(raw["detection_timing"]),
            "caveats": tuple(raw["caveats"]),
        }
    )
