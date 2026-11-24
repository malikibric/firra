-- Data policy is enforced on stored data and on reads.
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as select tests.create_user('owner@example.com') as owner_id;
grant select on fx to authenticated;
create temp table t (k text primary key, v uuid) on commit drop;
grant all on t to authenticated;

select tests.authenticate_as((select owner_id from fx));
insert into t select 'ws', public.create_workspace('A');
insert into t select 'acme', public.create_client((select v from t where k = 'ws'), 'Acme');
insert into t select 'globex', public.create_client((select v from t where k = 'ws'), 'Globex');

select tests.as_postgres();
with c as (
  insert into public.connection (workspace_id, provider, mode, account_label)
  values ((select v from t where k = 'ws'), 'email_upload', 'upload', 'export.mbox') returning id)
insert into t select 'conn', id from c;
insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id, policy_at_ingest,
                                  occurred_at, direction, word_count, body_clean, raw_object_uri)
select (select v from t where k = 'ws'), (select v from t where k = 'conn'), 'email_upload', 'm-' || n, 'email',
       (select v from t where k = case when n <= 2 then 'acme' else 'globex' end), 'full', now(), 'in', 10,
       'hello', 'gs://bucket/raw/' || n
  from generate_series(1, 4) n;

-- Storage-level guarantees.
select throws_ok(
  format($$insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, policy_at_ingest, occurred_at, direction, body_clean)
           values (%L, %L, 'email_upload', 'x', 'email', 'metadata_only', now(), 'in', 'body')$$,
         (select v from t where k = 'ws'), (select v from t where k = 'conn')),
  '23514', null, 'metadata_only records cannot hold a body');
select throws_ok(
  format($$insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, policy_at_ingest, occurred_at, direction)
           values (%L, %L, 'email_upload', 'y', 'email', 'excluded', now(), 'in')$$,
         (select v from t where k = 'ws'), (select v from t where k = 'conn')),
  '23514', null, 'excluded data cannot be stored');

select tests.authenticate_as((select owner_id from fx));
select is((select count(*)::int from public.source_record), 4, 'member sees all records');

select lives_ok(format($$select public.set_client_policy(%L, 'metadata_only')$$, (select v from t where k = 'acme')),
  'switch acme to metadata_only');
select is((select count(*)::int from public.source_record where client_id = (select v from t where k = 'acme') and body_purged_at is not null), 2,
  'acme bodies and raw references removed (users cannot select the body columns any more)');

select lives_ok(format($$select public.set_client_policy(%L, 'excluded')$$, (select v from t where k = 'globex')),
  'switch globex to excluded');
select tests.as_postgres();
select is((select count(*)::int from public.source_record where client_id = (select v from t where k = 'globex')), 0,
  'globex records deleted');

-- Read path hides excluded clients even if a row existed.
update public.client set policy = 'full' where id = (select v from t where k = 'globex');
insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id, policy_at_ingest, occurred_at, direction)
values ((select v from t where k = 'ws'), (select v from t where k = 'conn'), 'email_upload', 'late', 'email',
        (select v from t where k = 'globex'), 'metadata_only', now(), 'in');
update public.client set policy = 'excluded' where id = (select v from t where k = 'globex');
select tests.authenticate_as((select owner_id from fx));
select is((select count(*)::int from public.source_record where client_id = (select v from t where k = 'globex')), 0,
  'records of excluded clients are invisible to users');
select is((select action from public.audit_log where action = 'client.policy_changed' order by created_at desc limit 1),
  'client.policy_changed', 'policy changes are audited');

select * from finish();
rollback;
