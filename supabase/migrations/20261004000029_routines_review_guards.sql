-- T43a follow-up from the independent review of 20261004000028 (the original task specs, #123 comment):
-- two defence-in-depth guards on what the worker writes. Existing rows are unaffected (all new
-- columns were null or empty).

-- The weekly budget index and `unique (workspace_id, week, dedupe_key)` both assume `week` is the
-- Monday of `surfaced_on`; make a worker bug unable to store a card that breaks it.
alter table public.decision_card
  add constraint decision_card_week_is_monday_of_surfaced_on
  check (surfaced_on is null or week = surfaced_on - (extract(isodow from surfaced_on)::int - 1));

-- `routine_run.counts` is numbers only (no ids, no text): enforce it instead of only saying so.
alter table public.routine_run
  add constraint routine_run_counts_are_numbers
  check (not jsonb_path_exists(counts, '$.* ? (@.type() != "number")'));
