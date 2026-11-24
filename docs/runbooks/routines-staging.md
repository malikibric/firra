# Routines on staging: what is deployed and how to check it

> **Local v1:** routines run in the engine's local scheduler (sweep with catch-up after sleep), switched
> through the audited `set_routine_enabled` RPC. The Cloud Scheduler / Cloud Tasks deploy flow below is
> the legacy staging path and stays only until phase F9.

Milestone E (`docs/plans/e-routines.md`). **There is nothing extra to deploy**: every push to `main` that touches
`apps/api`, `services/worker`, `supabase`, `packages`, the lockfiles or the two deploy workflows runs "Deploy
staging" (`.github/workflows/deploy-staging.yml`, which calls `deploy.yml`): it builds both images, runs
`supabase db push` against staging, deploys Cloud Run, checks health and, only when the repository variable
`SMOKE_WORKSPACE_ID` is set, runs the task round-trip (otherwise it warns and skips). The routines migrations (`20261004000028`, `…029`), the runner and the sweep step went out
this way. The 15-minute Cloud Scheduler job `sync-sweep` already calls the worker's `/maintenance/sweep`, which
now also starts due routines, so no Scheduler or Terraform change is needed.

## 1. Confirm the deploy and the migrations

```bash
gh run list --workflow "Deploy staging" --limit 3        # newest run: completed / success, on the commit you expect
```

In the Supabase SQL editor of the staging project (read-only checks):

```sql
select version from supabase_migrations.schema_migrations order by version desc limit 3;   -- 20261004000029 first
select kind, enabled, definition_version from public.routine;                              -- see section 2
```

## 2. Routine rows appear only for workspaces with a sync connection

The sweep creates the three routine rows (`morning_scan`, `commitment_watch`, `pre_meeting_brief`) for every
workspace that has an **active `gmail` or `google_calendar` connection** that holds a token (or is
`org_delegated`). A workspace with only Outlook or
uploads gets none until an owner or admin switches one with the RPC `set_routine_enabled`. There is no screen or
`/v1` endpoint for that yet (T50e and the Mac settings follow); until then use the RPC from the SQL editor while
signed in as the owner, or ask for the row to be created. `pre_meeting_brief` is only a switch: the notch brief
still comes from the `brief.meetings` sweep.

## 3. When a routine runs, and what a run needs

- `morning_scan` 07:30 and `commitment_watch` 08:00, **workspace-local time, Monday to Friday**
  (`services/worker/config/routines.yaml`). The workspace `timezone` must be a valid IANA name; an unknown one
  falls back to UTC and the sweep log says so (`unknown_timezone`).
- A run reads **stored assessments, signal history, renewal dates and commitments**. A workspace with no live
  `account_assessment` rows (nothing extracted or evaluated yet) normally produces no cards, which is correct, not
  a fault. It is not a guarantee of zero items: renewal-deadline and signal candidates do not need an assessment, but
  a card is only written for a client that has one, so `counts.skipped_no_assessment` counts the rest. Check:
  `select count(*) from public.account_assessment where run_kind = 'live';`.
- The first run looks one day back, so enabling routines on an old workspace does not flood it.

## 4. Look at what happened (counts only, no text)

```sql
select r.kind, rr.scheduled_for, rr.status, rr.counts, rr.error_class
from public.routine_run rr join public.routine r on r.id = rr.routine_id
order by rr.scheduled_for desc, r.kind limit 10;

select kind, importance, reason_code, surfaced_on, demoted_reason
from public.decision_card where routine_run_id is not null order by created_at desc limit 20;
```

A normal run's `counts` holds `items`, `important`, `demoted`, `suppressed`, `skipped_no_assessment`; a run that
did nothing holds one marker instead (`disabled`, `no_source` or `not_claimed`). At most **3 `important` per
workspace and ISO week**; an `important` over the cap is stored as `attention` with `demoted_reason = 'budget'`.
In Cloud Logging the worker writes `maintenance.routines` (count, unknown_timezone) on every sweep and
`routines.run` per run.

## 5. Failure modes and what to do

| Symptom | Meaning | Action |
|---|---|---|
| `routine_run.status = 'running'` for more than 15 minutes | the worker died mid-run | nothing: the next delivery reclaims it after 15 minutes; if no delivery comes, the sweep re-dispatches the task once it has been idle for 30 minutes |
| `status = 'failed'`, today | the run raised (`error_class` says which class) | Cloud Tasks retries; if all attempts fail it stays failed until tomorrow; read the worker log by `routine_id` |
| no run row for a due routine | routine disabled, no sync connection, or local time not reached | section 2 and 3 |
| `counts.skipped_no_assessment > 0` | a client changed but has no live assessment | expected for brand-new clients |

To stop a routine: `select public.set_routine_enabled('<workspace id>', 'morning_scan', false);` as owner or admin.

## 6. What the Mac does

The app polls every 15 minutes. If the first page of `GET /v1/workspaces/:id/decisions?status=all` contains any card
with an `importance`, it shows grouped cards (Important, Worth attention, FYI, This week) and notifies **only** for
`important` ones, each once per signed-in user on that Mac; otherwise the old escalation notices stay in charge. Nothing notifies while
the Mac is offline or a read fails.

## 7. What this does not prove

Free eval on synthetic data only (the design notes): real mail, real extraction and a partner's own
history are untested. The exit of milestone E is two design partners keeping notifications on for two weeks with at
least half of the important items rated useful.
