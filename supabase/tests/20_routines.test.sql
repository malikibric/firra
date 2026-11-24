-- T43a: routines. Owner/admin switch through an audited RPC, no direct user writes, isolation of
-- routines and runs, one run per routine and date, the fields of a routine's decision cards, and
-- that the existing card behaviour (policy, feedback) carries over to the new kinds.
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as
select tests.create_user('owner-a@example.com') as a, tests.create_user('owner-b@example.com') as b,
       tests.create_user('plain@example.com') as p;
grant select on fx to authenticated;
create temp table t (k text primary key, v uuid) on commit drop;
grant all on t to authenticated;

select tests.authenticate_as((select a from fx));
insert into t select 'ws_a', public.create_workspace('A');
insert into t select 'acme', public.create_client((select v from t where k = 'ws_a'), 'Acme');
insert into t select 'globex', public.create_client((select v from t where k = 'ws_a'), 'Globex');
select tests.authenticate_as((select b from fx));
insert into t select 'ws_b', public.create_workspace('B');

-- The switch ------------------------------------------------------------------------------------
select tests.authenticate_as((select a from fx));
select lives_ok(format('select public.set_routine_enabled(%L, ''morning_scan'', true)', (select v from t where k = 'ws_a')),
  'owner enables a routine');
select is((select count(*)::int from public.routine where workspace_id = (select v from t where k = 'ws_a')), 1,
  'the routine row exists');
select lives_ok(format('select public.set_routine_enabled(%L, ''morning_scan'', false)', (select v from t where k = 'ws_a')),
  'owner disables it again');
select is((select count(*)::int from public.routine where workspace_id = (select v from t where k = 'ws_a')), 1,
  'switching is an upsert: still one row per workspace and kind');
select is((select enabled from public.routine where workspace_id = (select v from t where k = 'ws_a')), false,
  'and it is disabled');
insert into t select 'routine_a', id from public.routine where workspace_id = (select v from t where k = 'ws_a');

select tests.as_postgres();
select is((select count(*)::int from public.audit_log
            where workspace_id = (select v from t where k = 'ws_a') and action = 'routine.enabled_changed'
              and target_id = (select v from t where k = 'routine_a')), 2,
  'every change is audited');
select is((select metadata ->> 'kind' from public.audit_log
            where workspace_id = (select v from t where k = 'ws_a') and action = 'routine.enabled_changed'
            order by created_at desc limit 1), 'morning_scan', 'the audit row names the routine kind only');

select tests.authenticate_as((select b from fx));
select throws_ok(format('select public.set_routine_enabled(%L, ''morning_scan'', true)', (select v from t where k = 'ws_a')),
  '42501', 'forbidden', 'the owner of another workspace cannot switch it');
select tests.authenticate_as((select p from fx));
select throws_ok(format('select public.set_routine_enabled(%L, ''morning_scan'', true)', (select v from t where k = 'ws_a')),
  '42501', 'forbidden', 'a signed-in non-member cannot switch it');
select tests.as_anon();
select throws_ok($$select public.set_routine_enabled('00000000-0000-0000-0000-000000000000', 'morning_scan', true)$$,
  '42501', null, 'anon cannot execute it');

-- No direct writes -----------------------------------------------------------------------------
select tests.as_postgres();
select tests.authenticate_as((select a from fx));
select throws_ok(format('insert into public.routine (workspace_id, kind) values (%L, ''commitment_watch'')', (select v from t where k = 'ws_a')),
  '42501', null, 'users cannot insert a routine');
select throws_ok(format('update public.routine set enabled = true where id = %L', (select v from t where k = 'routine_a')),
  '42501', null, 'users cannot update a routine');
select throws_ok(format('insert into public.routine_run (workspace_id, routine_id, scheduled_for) values (%L, %L, ''2026-10-05'')',
  (select v from t where k = 'ws_a'), (select v from t where k = 'routine_a')),
  '42501', null, 'users cannot insert a run');

-- Runs (service role) ---------------------------------------------------------------------------
select tests.as_postgres();
with r as (
  insert into public.routine_run (workspace_id, routine_id, scheduled_for, status, counts)
  values ((select v from t where k = 'ws_a'), (select v from t where k = 'routine_a'), '2026-10-05', 'succeeded',
          '{"items": 2, "important": 1}') returning id)
insert into t select 'run_a', id from r;
select throws_ok(format('insert into public.routine_run (workspace_id, routine_id, scheduled_for) values (%L, %L, ''2026-10-05'')',
  (select v from t where k = 'ws_a'), (select v from t where k = 'routine_a')),
  '23505', null, 'a second run for the same routine and date is refused');
select lives_ok(format('insert into public.routine_run (workspace_id, routine_id, scheduled_for) values (%L, %L, ''2026-10-06'')',
  (select v from t where k = 'ws_a'), (select v from t where k = 'routine_a')),
  'the next date is a new run');
select throws_ok(format('insert into public.routine_run (workspace_id, routine_id, scheduled_for, counts) values (%L, %L, ''2026-10-07'', ''[1]'')',
  (select v from t where k = 'ws_a'), (select v from t where k = 'routine_a')),
  '23514', null, 'counts must be an object');
select throws_ok(format('insert into public.routine_run (workspace_id, routine_id, scheduled_for) values (%L, %L, ''2026-10-08'')',
  (select v from t where k = 'ws_b'), (select v from t where k = 'routine_a')),
  '23503', null, 'a run cannot point at another workspace''s routine');

select tests.authenticate_as((select a from fx));
select is((select count(*)::int from public.routine_run where workspace_id = (select v from t where k = 'ws_a')), 2,
  'the owner reads the runs of the workspace');
select tests.authenticate_as((select b from fx));
select is((select count(*)::int from public.routine where workspace_id = (select v from t where k = 'ws_a')), 0,
  'the owner of another workspace sees no routine of A');
select is((select count(*)::int from public.routine_run where workspace_id = (select v from t where k = 'ws_a')), 0,
  'and no run of A');
select tests.authenticate_as((select p from fx));
select is((select count(*)::int from public.routine where workspace_id = (select v from t where k = 'ws_a')), 0,
  'a non-member sees no routine');
select is((select count(*)::int from public.routine_run where workspace_id = (select v from t where k = 'ws_a')), 0,
  'and no run');

-- Cards created by a routine -------------------------------------------------------------------
select tests.as_postgres();
create function tests.add_card(p_client text, p_dedupe text, p_kind text, p_importance text,
                               p_reason text, p_demoted text default null, p_run uuid default null)
returns void
language plpgsql
as $$
begin
  insert into public.decision_card (workspace_id, client_id, week, kind, title, body, locale, score, priority, inputs,
    model, prompt_version, dedupe_key, play, facts, interpretation, recommendation, strength,
    importance, surfaced_on, reason_code, demoted_reason, routine_run_id)
  values ((select v from t where k = 'ws_a'), (select v from t where k = p_client), '2026-10-05', p_kind::public.decision_kind,
    'Risk rose', 'Risk is now elevated', 'en', 1, 1, '{}', 'deterministic', 'facts.v1', p_dedupe, 'fix_first',
    '[{"id":"f1","text":"Client emails fell to 2 from 9","basis":"metadata"}]', 'i', 'r', 'weak',
    p_importance::public.card_importance, '2026-10-06', p_reason, p_demoted,
    coalesce(p_run, (select v from t where k = 'run_a')));
end;
$$;

select lives_ok($$select tests.add_card('acme', 'c1', 'state_change', 'important', 'risk_rose_elevated')$$,
  'a state_change card with importance is stored');
select lives_ok($$select tests.add_card('acme', 'c2', 'commitment_watch', 'attention', 'commitment_late', 'budget')$$,
  'a demoted card is an attention card with a reason');
select lives_ok($$select tests.add_card('globex', 'c3', 'expansion', 'fyi', 'expansion_strong')$$,
  'an expansion card is stored');
select throws_ok($$select tests.add_card('acme', 'c4', 'state_change', 'important', 'risk_rose_elevated', 'budget')$$,
  '23514', null, 'only an attention card can carry a demotion');
select throws_ok($$select tests.add_card('acme', 'c5', 'state_change', 'attention', 'Bad Code')$$,
  '23514', null, 'a reason code is a lower-case code');
select throws_ok(format($sql$
  insert into public.decision_card (workspace_id, client_id, week, kind, title, body, locale, score, priority, inputs,
    model, prompt_version, dedupe_key, play, facts, interpretation, recommendation, strength, routine_run_id)
  values (%L, %L, '2026-10-05', 'state_change', 't', 'b', 'en', 1, 1, '{}', 'deterministic', 'facts.v1', 'c6', 'fix_first',
    '[{"id":"f1","text":"x","basis":"metadata"}]', 'i', 'r', 'weak', %L) $sql$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme'), (select v from t where k = 'run_a')),
  '23514', null, 'a card of a run must carry importance, surfaced_on and reason_code');
select lives_ok($$
  insert into public.decision_card (workspace_id, client_id, week, kind, title, body, locale, score, priority, inputs,
    model, prompt_version, dedupe_key, play, facts, interpretation, recommendation, strength)
  values ((select v from t where k = 'ws_a'), (select v from t where k = 'acme'), '2026-10-05', 'risk', 't', 'b', 'en', 1, 1,
    '{}', 'm', 'cards.v1', 'weekly-old', 'fix_first', '[{"id":"f1","text":"x","basis":"metadata"}]', 'i', 'r', 'weak')$$,
  'the older weekly cards (no run, no importance) are unchanged');

select tests.authenticate_as((select a from fx));
select is((select count(*)::int from public.decision_card
            where workspace_id = (select v from t where k = 'ws_a') and importance = 'important'), 1,
  'the owner reads the important card');
select lives_ok(format('select public.record_decision_feedback(%L, ''useful'', ''taken'')',
  (select id from public.decision_card where workspace_id = (select v from t where k = 'ws_a') and dedupe_key = 'c1')),
  'feedback works on a routine card');
select is((select status::text from public.decision_card
            where workspace_id = (select v from t where k = 'ws_a') and dedupe_key = 'c1'), 'done',
  'and moves it like any card');
select tests.authenticate_as((select b from fx));
select is((select count(*)::int from public.decision_card where workspace_id = (select v from t where k = 'ws_a')), 0,
  'the owner of another workspace reads none of them');

-- A deleted run keeps its cards -----------------------------------------------------------------
select tests.as_postgres();
delete from public.routine_run where id = (select v from t where k = 'run_a');
select is((select count(*)::int from public.decision_card
            where workspace_id = (select v from t where k = 'ws_a') and dedupe_key = 'c1' and routine_run_id is null
              and importance = 'important'), 1,
  'deleting a run leaves its cards, detached');

-- Policy carries over ---------------------------------------------------------------------------
select tests.authenticate_as((select a from fx));
select lives_ok(format('select public.set_client_policy(%L, ''excluded'')', (select v from t where k = 'globex')),
  'the owner excludes a client');
select tests.as_postgres();
select is((select count(*)::int from public.decision_card where client_id = (select v from t where k = 'globex')), 0,
  'excluding a client deletes its routine cards');
select is((select count(*)::int from public.decision_card where client_id = (select v from t where k = 'acme')
            and dedupe_key in ('c1', 'c2')), 2, 'the other client keeps its cards');


-- Review additions (independent review of T43a) -------------------------------------------------
-- A card cannot point at another workspace's run.
select tests.as_postgres();
insert into t select 'routine_b', id from (select id from public.routine where false) x;
with r as (
  insert into public.routine (workspace_id, kind) values ((select v from t where k = 'ws_b'), 'morning_scan') returning id)
insert into t select 'routine_b', id from r;
with r as (
  insert into public.routine_run (workspace_id, routine_id, scheduled_for)
  values ((select v from t where k = 'ws_b'), (select v from t where k = 'routine_b'), '2026-10-05') returning id)
insert into t select 'run_b', id from r;
select throws_ok(
  format($$select tests.add_card('acme', 'x1', 'state_change', 'fyi', 'risk_fell', null, %L)$$, (select v from t where k = 'run_b')),
  '23503', null, 'a card of workspace A cannot cite a run of workspace B');

-- week is the Monday of surfaced_on, counts are numbers.
select throws_ok($$
  insert into public.decision_card (workspace_id, client_id, week, kind, title, body, locale, score, priority, inputs,
    model, prompt_version, dedupe_key, play, facts, interpretation, recommendation, strength, surfaced_on)
  values ((select v from t where k = 'ws_a'), (select v from t where k = 'acme'), '2026-10-12', 'state_change', 't', 'b',
    'en', 1, 1, '{}', 'deterministic', 'facts.v1', 'wk1', 'fix_first', '[{"id":"f1","text":"x","basis":"metadata"}]',
    'i', 'r', 'weak', '2026-10-06')$$,
  '23514', null, 'week must be the Monday of surfaced_on');
select throws_ok(format($sql$
  insert into public.routine_run (workspace_id, routine_id, scheduled_for, counts)
  values (%L, %L, '2026-10-09', '{"items": 1, "note": "text"}')$sql$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'routine_a')),
  '23514', null, 'counts may hold numbers only');
select throws_ok(format($sql$
  insert into public.routine_run (workspace_id, routine_id, scheduled_for, counts, error_class)
  values (%L, %L, '2026-10-10', '{}', 'cannot reach jane@example.com')$sql$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'routine_a')),
  '23514', null, 'error_class is a class name, not a message');
select lives_ok(format($sql$
  insert into public.routine_run (workspace_id, routine_id, scheduled_for, counts, error_class)
  values (%L, %L, '2026-10-13', '{}', 'ValueError')$sql$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'routine_a')),
  'a class name is accepted');

-- Read-only for users: update, delete and anon.
select tests.authenticate_as((select a from fx));
select throws_ok(format('update public.routine_run set status = ''failed'' where id = %L', (select v from t where k = 'run_a')),
  '42501', null, 'users cannot update a run');
select throws_ok(format('delete from public.routine_run where workspace_id = %L', (select v from t where k = 'ws_a')),
  '42501', null, 'users cannot delete a run');
select throws_ok(format('delete from public.routine where id = %L', (select v from t where k = 'routine_a')),
  '42501', null, 'users cannot delete a routine');
select tests.as_anon();
select throws_ok('select count(*) from public.routine', '42501', null, 'anon cannot read routines');
select throws_ok('select count(*) from public.routine_run', '42501', null, 'anon cannot read runs');

-- An admin can switch a routine, like an owner.
select tests.as_postgres();
create temp table adm on commit drop as select tests.create_user('admin-a@example.com') as u;
grant select on adm to authenticated;
insert into public.membership (workspace_id, user_id, role)
values ((select v from t where k = 'ws_a'), (select u from adm), 'admin');
select tests.authenticate_as((select u from adm));
select lives_ok(format('select public.set_routine_enabled(%L, ''commitment_watch'', true)', (select v from t where k = 'ws_a')),
  'an admin can switch a routine');
select is((select count(*)::int from public.routine where workspace_id = (select v from t where k = 'ws_a')), 2,
  'and sees both routines of the workspace');

-- metadata_only: what happens to a routine card when its client leaves `full`.
select tests.as_postgres();
with c as (insert into public.client (workspace_id, name) values ((select v from t where k = 'ws_a'), 'Initech') returning id)
insert into t select 'initech', id from c;
with r as (
  insert into public.routine_run (workspace_id, routine_id, scheduled_for)
  values ((select v from t where k = 'ws_a'), (select v from t where k = 'routine_a'), '2026-10-12') returning id)
insert into t select 'run_c', id from r;
select lives_ok(
  format($$select tests.add_card('initech', 'meta1', 'state_change', 'attention', 'risk_rose_watch', null, %L)$$,
    (select v from t where k = 'run_c')),
  'a routine card for a full client');
select tests.authenticate_as((select a from fx));
select lives_ok(format('select public.set_client_policy(%L, ''metadata_only'')', (select v from t where k = 'initech')),
  'the owner moves the client to metadata_only');
select tests.as_postgres();
select is((select count(*)::int from public.decision_card where client_id = (select v from t where k = 'initech')), 0,
  'leaving full deletes the routine cards of that client (they may have cited quotes)');

select * from finish();
rollback;
