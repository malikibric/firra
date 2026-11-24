# Routine engine (milestone E): the pure functions

> **Cloud-first history.** The order of work is now `docs/plans/local-v1.md`.


Spec for T43b (implementation) and T43c (oracle cases). Both are written from this file, not from
each other. Parent plan: `docs/plans/e-routines.md` (E-D4 to E-D7). Everything here is a pure
function: no database, no clock, no model, no logging of content.

Module `firra.routines` in `services/worker`. Config `services/worker/config/routines.yaml`.
Risk states reuse `firra.assessment.config` (`RiskState`, `risk_rank`: stable 0, watch 1, elevated 2,
high 3).

## Config (`routines.v1`)

```yaml
version: routines.v1
budget_per_week: 3 # important items per workspace and ISO week (E-D6)
wrong_hold_days: 30 # E-D7
notice_close_days: 14
notice_near_days: 30
schedules: # workspace-local time; weekdays are ISO (1 = Monday)
  morning_scan: { hour: 7, minute: 30, weekdays: [1, 2, 3, 4, 5] }
  commitment_watch: { hour: 8, minute: 0, weekdays: [1, 2, 3, 4, 5] }
```

The loader rejects unknown keys, non-positive numbers, hours outside 0–23, minutes outside 0–59 and
weekdays outside 1–7.

## Types

```python
Trigger = Literal[
    "risk_up",
    "risk_down",
    "expansion_up",
    "signal_opened",
    "signal_resolved",
    "commitment_late",
    "notice_close",
]
Importance = Literal["fyi", "attention", "important"]  # rank 0, 1, 2
Kind = Literal["state_change", "commitment_watch", "expansion"]


@dataclass(frozen=True, slots=True)
class Candidate:  # one stored change the routine found
    client_id: UUID
    kind: Kind
    trigger: Trigger
    as_of: date
    risk_to: RiskState | None = None  # risk_up, risk_down
    expansion_to: ExpansionState | None = None  # expansion_up
    severity: Literal["low", "medium", "high"] | None = None  # signal_opened
    owner_side: Literal["firm", "client"] | None = None  # commitment_late
    evidence_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class ClientContext:  # what is true about the client on `as_of`
    client_id: UUID
    policy: Literal["full", "metadata_only"]  # an excluded client never reaches the engine
    risk: RiskState
    days_to_notice: int | None  # days to the notice deadline; None when no renewal is known


@dataclass(frozen=True, slots=True)
class Prior:  # an earlier routine card of the same client
    kind: Kind
    reason_code: str
    verdict: Literal["useful", "already_known", "wrong", "not_now"] | None
    surfaced_on: date
    evidence_ids: frozenset[UUID]
    risk_rank: int  # the client's risk rank when that card was built


@dataclass(frozen=True, slots=True)
class Decision:  # one card to create
    client_id: UUID
    kind: Kind
    reason_code: str
    importance: Importance
    demoted_reason: Literal["budget"] | None
    also: tuple[str, ...]  # reason codes of the other candidates folded in
    evidence_ids: frozenset[UUID]
    risk_rank: int
    days_to_notice: int | None
    as_of: date


@dataclass(frozen=True, slots=True)
class Plan:
    decisions: tuple[Decision, ...]  # sorted, see `apply_budget`
    counts: dict[str, int]  # items, important, demoted, suppressed
```

## Functions

### `classify(candidate, context, config) -> tuple[Importance, str] | None`

First match wins. "Risk at least X" compares `risk_rank(context.risk)`.

| Trigger | Condition | Importance | `reason_code` |
|---|---|---|---|
| `risk_up` | `risk_to` is `elevated` or `high` | important | `risk_rose_elevated` |
| `risk_up` | `risk_to` is `watch` | attention | `risk_rose_watch` |
| `risk_up` | anything else | fyi | `risk_rose_other` |
| `risk_down` | any | fyi | `risk_fell` |
| `expansion_up` | `expansion_to` is `strong` | attention | `expansion_strong` |
| `expansion_up` | anything else | fyi | `expansion_hypothesis` |
| `signal_opened` | `severity` is `high` and risk at least `watch` | attention | `signal_high` |
| `signal_opened` | anything else | fyi | `signal_opened` |
| `signal_resolved` | any | fyi | `signal_resolved` |
| `commitment_late` | `owner_side` is `firm` and risk at least `elevated` | important | `commitment_late_at_risk` |
| `commitment_late` | `owner_side` is `firm` | attention | `commitment_late` |
| `commitment_late` | anything else (`client`, or `None`: an unknown owner is never raised above FYI) | fyi | `client_commitment_late` |
| `notice_close` | risk below `watch`, or `days_to_notice` is None or negative | none (returns `None`) | |
| `notice_close` | `days_to_notice` ≤ `notice_close_days` | important | `notice_deadline_close` |
| `notice_close` | `days_to_notice` ≤ `notice_near_days` | attention | `notice_deadline_near` |
| `notice_close` | anything else | none (returns `None`) | |

The order of the rows above is the **reason order** used to break ties in `merge` (earlier row =
stronger). `notice_close` candidates read `days_to_notice` from the context, not from the candidate.

### `merge(candidates, context, config) -> Decision | None`

All candidates are for one client. Classify each; drop those that return `None`; none left → `None`.
The winner is the first by this key: importance descending, reason order ascending, `as_of`
descending, number of evidence ids descending, `kind` alphabetical ascending, then the sorted list
of evidence ids as strings ascending (so equal candidates still give one answer). The decision
takes the winner's `kind` and `reason_code`; `also` is the sorted list of the other kept candidates'
distinct reason codes (the winner's own code excluded); `evidence_ids` is the union over **all**
kept candidates; `risk_rank = risk_rank(context.risk)`; `days_to_notice` and `as_of` from the context
and the winner. A `metadata_only` client's decision always has empty `evidence_ids`.

### `is_suppressed(decision, priors, config, today) -> bool`

Only priors with the same `kind` and `reason_code` and a `verdict` of `not_now`, `already_known` or
`wrong` count; `useful` and no verdict never suppress. Use the first by this key: latest `surfaced_on`; then `wrong` before `already_known` before
`not_now`; then the lowest `risk_rank`; then the fewest evidence ids; then the sorted evidence ids as
strings ascending. (When feedback is ambiguous the engine prefers to surface: a missed warning costs
more than an extra FYI.) Let `worse = decision.risk_rank >
prior.risk_rank` and `new = not decision.evidence_ids <= prior.evidence_ids`.

- `not_now`, `already_known`: suppressed unless `worse` or `new`.
- `wrong`: suppressed when `not worse` and `(today - prior.surfaced_on).days < wrong_hold_days`
  (new evidence alone does not lift it inside the hold); from `wrong_hold_days` on it behaves like
  `not_now`.

### `apply_budget(decisions, important_used, config) -> tuple[Decision, ...]`

`important_used` is the number of `important` cards the workspace already has in the ISO week of the
decisions (the caller counts; a card demoted earlier is `attention` and is not counted). Sort the
important decisions by `(-risk_rank, days_to_notice ascending with None last, as_of descending,
client_id as string ascending)`. The first `max(0, budget_per_week - important_used)` stay
important; every other one becomes `importance = "attention"` with `demoted_reason = "budget"`. Other
decisions are unchanged. The result is sorted by importance rank descending, then by the same key;
equal keys keep a stable order.

### `plan(candidates, contexts, priors, important_used, config, today) -> Plan`

`candidates: Mapping[UUID, Sequence[Candidate]]`, `contexts: Mapping[UUID, ClientContext]`,
`priors: Mapping[UUID, Sequence[Prior]]` (a missing key means none). For each client with a context:
`merge`, then `is_suppressed`; then `apply_budget` on the survivors. A client with candidates but no
context is skipped (counts as nothing). `counts` = `{"items": len(decisions), "important": number
still important, "demoted": number demoted, "suppressed": number dropped by `is_suppressed`}`.

## Invariants every implementation must keep

1. Deterministic: the same input gives the same `Plan`, whatever the dict order.
2. Never more than `budget_per_week - important_used` decisions come out `important`.
3. A demoted decision is `attention` with `demoted_reason = "budget"`; no other decision has a
   `demoted_reason`.
4. No decision for a client without a context; none for a candidate that classifies to `None`.
5. A `metadata_only` client's decisions have no evidence ids.
6. Nothing is ever dropped silently by the budget: `items == len(candidates after suppression)`.
