# Milestone E: routines (plan)

> **Cloud-first history.** The order of work is now `docs/plans/local-v1.md`.


Roadmap E (`docs/roadmap.md`). Written 2026-10-03, after D's code gaps closed (T91–T97). Exit of D
(two design partners using the app for two weeks) is measured in parallel, not waited for.

Goal: the right item reaches the founder at the right time without spam. First-party routines read
what the worker already stored (assessments, changes, commitments, outcomes, feedback), rank each
item by importance, and surface it: **FYI** stays in Firra, **worth attention** joins the brief
(the Today / What changed list), **important** becomes a native notification, at most three a week
per workspace. No routine reads raw mail or calls a model (north star rule 5: LLMs word, they do not
decide).

## Decisions

| # | Topic | Decision | Status |
|---|---|---|---|
| E-D1 | What an item is | A surfaced item **is a `decision_card`** (T32, deferred until now, is minimal here): it already has evidence links, the feedback loop (`decision_feedback`), status, `/v1/…/decisions`, the MCP `get_decisions` tool and the Mac Decisions view. New kinds `state_change`, `commitment_watch`, `expansion`; new columns `importance` (`fyi` \| `attention` \| `important`, null on the old weekly cards), `surfaced_on date`, `reason_code`, `demoted_reason` (`budget`), `routine_run_id`. Text is deterministic (`model = 'deterministic'`, `prompt_version = 'facts.v1'`); `week` is the Monday of `surfaced_on`, the dedupe key carries the date. | **approved by the owner 2026-10-03** (the alternative was a separate `routine_item` table plus a second feedback target) |
| E-D2 | Routine definition | First-party only. Table `routine (workspace_id, kind, enabled, params jsonb, definition_version)`, rows created lazily by the worker with defaults from `config/routines.yaml` (`routines.v1`). `kind` is a closed enum today (`morning_scan`, `commitment_watch`, `pre_meeting_brief`); a later `custom` kind with a stored plan is how natural-language routines fit, and is not built. Owner/admin enable or disable through an audited `SECURITY DEFINER` RPC (`set_routine_enabled`). | default |
| E-D3 | Runs | Table `routine_run (routine_id, scheduled_for date, status, job_id, counts jsonb, error_class)`, unique `(routine_id, scheduled_for)`, so a retry or a second sweep never doubles an item. The existing 15-minute `/maintenance/sweep` (Cloud Scheduler already calls it) also enqueues `routines.run {routine_id, scheduled_for}` for every enabled `morning_scan` and `commitment_watch` routine (`pre_meeting_brief` is only a switch, E-D10) whose workspace-local time has passed its hour and weekday and which has no run for that local date. No new Scheduler job and no Terraform. | default |
| E-D4 | Morning scan | Daily, weekdays, 07:30 workspace-local (`routines.yaml`). Items are the stored changes since the previous run (the `/v1/…/changes` sources: live `account_assessment` state changes and `signal_event`s, plus renewal notice-deadline crossings from the client's dates), one card per client and day, not one per signal. Exactly the clients whose state changed or crossed a threshold appear, nobody else. | default |
| E-D5 | Importance rules | Deterministic table in `routines.yaml`, first match wins, every result carries a `reason_code`. First cut (the full table is `e-routines-engine.md`): rise into `elevated`/`high` = important; `watch` rise, expansion reaching `strong`, a high-severity signal opened on a client at `watch` or worse, a firm commitment that just became late = attention (important when the client is already `elevated`); renewal notice deadline inside 14 days on a client at `watch` or worse = important, inside 30 days = attention; de-escalations, other opened signals, expansion hypotheses and resolved signals = fyi. Rules are versioned; numbers are tuned only with design-partner feedback. | default, tunable |
| E-D6 | Notification budget | At most **3 `important` per workspace per ISO week** (roadmap). Ranking inside the week: higher risk state, then closer notice deadline, then newer. Overflow is **demoted to `attention` with `demoted_reason = budget`**, never dropped. | roadmap rule |
| E-D7 | Feedback-aware suppression | An item whose `(client, kind, reason_code)` has a card with verdict `not_now`, `already_known` or `wrong` is not raised again unless it brings **new evidence ids or a worse state** than the one the earlier card was built from (`inputs` holds those ids and the state). `wrong` also holds for 30 days. A suppressed item is not created; the run's `counts` records how many were suppressed and why. | default |
| E-D8 | Delivery | No server push (no Apple Developer account, R-D8). The Mac app already checks every 15 minutes; it will read the server's `important` items instead of deriving escalations itself, and shows each once per signed-in user on that Mac (it remembers shown item ids locally). Budget is counted on the server, so two admins see the same three. Email and Slack stay in G. | default |
| E-D9 | Policy | `excluded` clients never get an item (the engine skips them; the policy change already deletes derived cards). `metadata_only` clients get items made of counts and dates only: no quote, no evidence link, facts all `basis = metadata` (the existing card trigger enforces it). | rules 2 and 8 |
| E-D10 | Pre-meeting brief | Already built (T45, notch). In E it is only a routine kind that owner/admin can switch (it has no runs, budget or feedback of its own); the notch path and `meeting_brief` table do not change. A brief never becomes a notification by itself. | default |
| E-D11 | Later routines | Weekly founder review, expansion scout, risk watch (roadmap E "then") wait for the first exit data. | deferred |

The pure engine (importance, suppression, budget) is specified in `docs/plans/e-routines-engine.md`.

## Work order

| Task | What | Agent | Depends on |
|---|---|---|---|
| T43a | Migration: enum values, `decision_card` columns, `routine`, `routine_run`, RLS, `set_routine_enabled`, pgTAP; db-types by hand (no Docker); contracts (`importance`, new kinds, decisions filter) | Claude | **owner OK on E-D1** |
| T43b | `firra/routines`: config loader, pure `importance()`, suppression and budget functions, `routines.yaml`; unit tests | Codex | T43a |
| T43c | Oracle cases for T43b written from this plan, not from the code: the importance table, suppression (not_now, wrong, new evidence, worse state), budget (4th important is demoted, week boundary, ranking ties) | opencode | plan only, runs ‖ with T43b |
| T43d | Runner: repository, card writer, `routines.run` handler, due-routine check in the 15-minute sweep (the `routines.run` contract and queue map land first, by Claude) | Codex | T43b, T43a |
| T44 | Morning scan routine (items from changes) | Codex | T43d |
| T46 | Commitment watch routine | Codex | T43d |
| T47e | `/v1` decisions: `importance` and `kind` filters and fields; MCP `get_decisions` follows | Codex, MCP security review Claude | T43a |
| T48e | Mac: Today sections by importance; `Notifier` reads server `important` items, local once-per-item memory | Codex | T47e |
| T49e | Free eval on Northbeam v3: replay rows day by day through the engine; morning scan lists exactly the changed clients; ≤ 3 important per week; suppression holds (evidence links are covered by T43d's tests, not by this eval) | Claude brief, Codex build | T44, T46 |
| T50e | `/v1` routines: list and switch (owner/admin) | Codex | T43a |
| T51e | Mac: Routines settings (three switches) | Codex | T50e |

## Evals (from the roadmap, all free, in CI)

- Morning scan on the planted Northbeam changes lists exactly the clients whose state changed or
  crossed a threshold that day.
- ≤ 3 `important` per workspace per week over the whole 12-month replay; overflow is demoted.
- An item marked "not now" is not re-raised without new evidence or a worse state.
- Items of `full` clients cite verified evidence (100 %); `metadata_only` items hold no quote.
- Exit (design partners, not CI): notifications kept on for two weeks; ≥ 50 % of `important` items
  rated useful. T34's usefulness report is how this is read.

## Owner actions

- Later: `supabase db push` of the T43a migration to staging before the first routine runs there. The sweep job already exists.

## Out of scope

Natural-language or user-built routines, any model call inside the engine, email or Slack delivery,
APNs push, per-user notification settings, ranking or scoring of the firm's people (rule 5).
