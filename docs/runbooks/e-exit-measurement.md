# Milestone E exit: measuring 2 weeks on a design partner's workspace

Read-only SQL for the exit in `docs/roadmap.md` (E): notifications kept on for two weeks and ≥ 50 %
of `important` items rated useful. Run in the partner's Supabase SQL editor; every query is
counts-only (no names, no notes). Replace `<workspace_id>` and `since` (the Monday the window starts).
Window `since` .. `since + 14` days, routine rows only (`routine_run_id is not null`).

## a. The routines stayed on

The server cannot see the Mac: these queries prove the routines ran and produced cards, not that the partner's
notifications stayed on. Ask the partner to confirm macOS notifications for Firra were allowed for the whole window.
The exit needs **two** partners; run every block once per workspace.

`routine_run` proves the scan ran, `decision_card` proves it produced items.

```sql
with p as (select '<workspace_id>'::uuid as ws, date '2026-09-21' as since)
select r.kind, r.enabled,
       count(*) filter (where rr.status = 'succeeded') as succeeded_runs,
       count(*) filter (where rr.status <> 'succeeded') as other_runs,
       min(rr.scheduled_for) as first_day, max(rr.scheduled_for) as last_day
from p
join public.routine r on r.workspace_id = p.ws
join public.routine_run rr on rr.workspace_id = r.workspace_id and rr.routine_id = r.id
 and rr.scheduled_for >= p.since and rr.scheduled_for < p.since + 14
group by r.kind, r.enabled order by r.kind;
```

Expect ~10 weekdays each (Mon-Fri) and `enabled = true`; fewer runs or `enabled = false` means the partner switched it off or the sweep failed — inspect `status`, `error_class` and `counts` for those days.

```sql
with p as (select '<workspace_id>'::uuid as ws, date '2026-09-21' as since)
select d.surfaced_on, count(*) as cards,
       count(*) filter (where d.importance = 'important') as important
from p
join public.decision_card d on d.workspace_id = p.ws and d.routine_run_id is not null
 and d.surfaced_on >= p.since and d.surfaced_on < p.since + 14
group by d.surfaced_on order by d.surfaced_on;
```

Cards on most days mean the pipeline kept producing; all-zero days with succeeded runs mean nothing changed in the stored picture.

## b. Share of important cards rated useful

By client id, never the client name; `rated` excludes `not_now` — the same denominator as `firra.learning` (`docs/runbooks/learning-metrics.md`), and the `grouping` row is the workspace total.

```sql
with p as (select '<workspace_id>'::uuid as ws, date '2026-09-21' as since),
feedback as (
  select distinct on (f.decision_card_id) f.decision_card_id, f.verdict
  from public.decision_feedback f, p
  where f.workspace_id = p.ws
  order by f.decision_card_id, f.updated_at desc
)
select d.client_id,
       count(*) as important_cards,
       count(*) filter (where f.verdict = 'useful') as useful,
       count(*) filter (where f.verdict in ('already_known', 'wrong')) as not_useful,
       count(*) filter (where f.verdict = 'not_now') as not_now,
       count(*) filter (where f.verdict in ('useful', 'already_known', 'wrong')) as rated,
       round(100.0 * count(*) filter (where f.verdict = 'useful')
             / nullif(count(*) filter (where f.verdict in ('useful', 'already_known', 'wrong')), 0), 1)
         as useful_pct
from p
join public.decision_card d on d.workspace_id = p.ws and d.importance = 'important'
 and d.surfaced_on >= p.since and d.surfaced_on < p.since + 14
left join feedback f on f.decision_card_id = d.id
group by rollup (d.client_id)
order by grouping(d.client_id), d.client_id;
```

Exit: the total row's `useful_pct >= 50.0`. `rated = 0` (`useful_pct` null) means the partner did not
rate — not a pass. Cards without feedback stay in `important_cards` but not in `rated`.

## c. The weekly budget held

At most 3 `important` per workspace per ISO week (`config/routines.yaml` `budget_per_week`).

```sql
with p as (select '<workspace_id>'::uuid as ws, date '2026-09-21' as since)
select date_trunc('week', d.surfaced_on)::date as iso_week_start,
       count(*) filter (where d.importance = 'important') as important,
       count(*) filter (where d.demoted_reason = 'budget') as demoted,
       count(*) as cards
from p
join public.decision_card d on d.workspace_id = p.ws and d.routine_run_id is not null
 and d.surfaced_on >= p.since and d.surfaced_on < p.since + 14
group by 1 order by 1;
```

`important` must be ≤ 3 in every row; `demoted` are the cards the budget pushed to `attention`.

## d. Demoted and suppressed

```sql
with p as (select '<workspace_id>'::uuid as ws, date '2026-09-21' as since)
select
  (select count(*) from public.decision_card d, p
     where d.workspace_id = p.ws and d.demoted_reason = 'budget'
       and d.surfaced_on >= p.since and d.surfaced_on < p.since + 14) as demoted_cards,
  (select coalesce(sum((rr.counts->>'suppressed')::int), 0) from public.routine_run rr, p
     where rr.workspace_id = p.ws and rr.status = 'succeeded'
       and rr.scheduled_for >= p.since and rr.scheduled_for < p.since + 14) as suppressed_items;
```

`demoted_cards`: `important` items over the cap, never dropped. `suppressed_items`: candidates E-D7
held back (a prior `wrong`/`already_known`/`not_now` without new evidence or a worse state).

## e. Read it with `learning-metrics.md`

`docs/runbooks/learning-metrics.md` runs `python -m firra.learning report --workspace <id> --as-of
<end>` and gives the all-time view by kind and play: usefulness, precision, lead time. The exit number
above is the 2-week, `important`-only, per-client cut; if they disagree, check the window and the
kind/play mix. Learning report: "which kinds earn their place"; these queries: "did E pass".

## How this was verified

Every block ran on the local Supabase (`postgresql://postgres:postgres@127.0.0.1:54322/postgres`)
inside a rolled-back transaction with a synthetic fixture: one workspace, two clients, two routines,
10 weekday `routine_run` rows each with `counts = {"suppressed": 2}`, nine cards across two ISO weeks
(three `important` each; two `attention` with `demoted_reason = 'budget'`), four feedback rows (two
`useful`, one `wrong`, one `not_now`). Results: a1 10/10 succeeded per kind; a2 cards on eight days;
b total `important_cards = 6, rated = 3, useful = 2, useful_pct = 66.7`; c `important = 3` per week;
d `demoted_cards = 2, suppressed_items = 20`. Fixture and `psql` command: PR description, not committed.
