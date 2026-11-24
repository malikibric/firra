-- Replay narratives follow the client's data policy like every other derived row.
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as
select tests.create_user('owner-a@example.com') as a, tests.create_user('owner-b@example.com') as b;
grant select on fx to authenticated;
create temp table t (k text primary key, v uuid) on commit drop;
grant all on t to authenticated;

select tests.authenticate_as((select a from fx));
insert into t select 'ws_a', public.create_workspace('A');
insert into t select 'acme', public.create_client((select v from t where k = 'ws_a'), 'Acme');
insert into t select 'globex', public.create_client((select v from t where k = 'ws_a'), 'Globex');
insert into t select 'initech', public.create_client((select v from t where k = 'ws_a'), 'Initech');
select tests.authenticate_as((select b from fx));
insert into t select 'ws_b', public.create_workspace('B');
insert into t select 'other', public.create_client((select v from t where k = 'ws_b'), 'Other');

select tests.as_postgres();
with r as (
  insert into public.replay_run (workspace_id, window_start, window_end, status)
  values ((select v from t where k = 'ws_a'), '2026-01-01', '2026-06-30', 'succeeded') returning id)
insert into t select 'run_a', id from r;
with r as (
  insert into public.replay_run (workspace_id, window_start, window_end, status)
  values ((select v from t where k = 'ws_b'), '2026-01-01', '2026-06-30', 'succeeded') returning id)
insert into t select 'run_b', id from r;
update public.replay_run
   set summary = jsonb_build_object('clients', (
         select jsonb_agg(jsonb_build_object('client_id', v, 'max_state', 'high')) from t
          where k in ('acme', 'globex', 'initech')), 'totals', '{}'::jsonb)
 where id = (select v from t where k = 'run_a');
insert into public.replay_narrative (workspace_id, replay_run_id, client_id, version, narrative)
select (select v from t where k = 'ws_a'), (select v from t where k = 'run_a'), v, 1, '{"transitions":[]}'
  from t where k in ('acme', 'globex', 'initech');
insert into public.replay_narrative (workspace_id, replay_run_id, client_id, version, narrative)
values ((select v from t where k = 'ws_b'), (select v from t where k = 'run_b'), (select v from t where k = 'other'), 1, '{}');

-- reads
select tests.authenticate_as((select a from fx));
select is((select count(*)::int from public.replay_narrative), 3, 'owner A sees the narratives of workspace A only');
select tests.authenticate_as((select b from fx));
select is((select count(*)::int from public.replay_narrative), 1, 'owner B sees the narrative of workspace B only');
select throws_ok(
  $$insert into public.replay_narrative (workspace_id, replay_run_id, client_id, version, narrative)
    values (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 1, '{}')$$,
  '42501', null, 'users cannot write narratives');

-- exclusion removes the client's narratives and nobody else's
select tests.authenticate_as((select a from fx));
select public.set_client_policy((select v from t where k = 'acme'), 'excluded');
select is((select count(*)::int from public.replay_narrative), 2, 'excluding a client removes its narratives');
select tests.as_postgres();
select is((select count(*)::int from public.replay_narrative where client_id = (select v from t where k = 'acme')), 0,
  'no narrative of the excluded client remains for any role');
select is((select count(*)::int from public.replay_narrative where workspace_id = (select v from t where k = 'ws_b')), 1,
  'other workspaces are untouched');

-- a metadata downgrade removes them too (the assessments they are built from go as well)
select tests.authenticate_as((select a from fx));
select public.set_client_policy((select v from t where k = 'globex'), 'metadata_only');
select tests.as_postgres();
select is((select count(*)::int from public.replay_narrative where client_id = (select v from t where k = 'globex')), 0,
  'a metadata_only downgrade removes the client''s narratives');
select is((select count(*)::int from public.replay_narrative where client_id = (select v from t where k = 'initech')), 1,
  'a client on full policy keeps its narrative');
select ok(not exists (select 1 from public.replay_run r, jsonb_array_elements(r.summary -> 'clients') e
                       where e ->> 'client_id' = (select v::text from t where k = 'globex')),
  'a metadata_only downgrade removes the client from the replay summaries');
select ok((select (summary ->> 'stale')::boolean from public.replay_run where id = (select v from t where k = 'run_a')),
  'and marks the summary stale');
select is((select jsonb_array_length(summary -> 'clients') from public.replay_run where id = (select v from t where k = 'run_a')), 1,
  'while the other client keeps its entry (acme was excluded earlier, initech remains)');
select is((select (derived_deleted -> 'replay_narrative')::int from (
  select metadata -> 'derived_deleted' as derived_deleted from public.audit_log
   where action = 'client.policy_changed' order by created_at desc limit 1) a), 1,
  'the policy audit row counts the deleted narratives');

-- deleting a client or a run cascades
delete from public.replay_run where id = (select v from t where k = 'run_a');
select is((select count(*)::int from public.replay_narrative where workspace_id = (select v from t where k = 'ws_a')), 0,
  'deleting a replay run deletes its narratives');

select * from finish();
rollback;
