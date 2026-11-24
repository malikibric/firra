-- Bodies are not readable by signed-in users; the vault columns exist.
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as select tests.create_user('owner@example.com') as owner_id;
grant select on fx to authenticated;
create temp table t (k text primary key, v text) on commit drop;
grant all on t to authenticated;

select tests.authenticate_as((select owner_id from fx));
insert into t select 'ws', public.create_workspace('A')::text;
insert into t select 'client', public.create_client((select v::uuid from t where k = 'ws'), 'Acme')::text;
select tests.as_postgres();
insert into public.connection (workspace_id, provider, mode, account_label, status)
values ((select v::uuid from t where k = 'ws'), 'email_upload', 'upload', 'export', 'active');
insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id,
                                  policy_at_ingest, occurred_at, direction, word_count, body_clean, raw_object_uri,
                                  vault_user_id)
values ((select v::uuid from t where k = 'ws'),
        (select id from public.connection where account_label = 'export'), 'email_upload', 'rec-1', 'email',
        (select v::uuid from t where k = 'client'), 'full', now(), 'in', 5, 'A body nobody may read here.',
        'gcs://bucket/raw/1', (select owner_id from fx));

select tests.authenticate_as((select owner_id from fx));
select is((select count(*)::int from public.source_record), 1, 'a member still sees the record');
select is((select count(*)::int from (select id, kind, metadata, deep_link, processing_state, vault_delivered_at from public.source_record) s), 1,
  'and its metadata, processing state and delivery state');
select throws_ok($$select body_clean from public.source_record$$, '42501', null, 'the body cannot be selected');
select throws_ok($$select raw_object_uri from public.source_record$$, '42501', null, 'nor the raw object reference');
select throws_ok($$select * from public.source_record$$, '42501', null, 'select * is refused as well');
select ok(not has_column_privilege('authenticated', 'public.source_record', 'body_clean', 'SELECT'), 'no body privilege');
select ok(not has_column_privilege('authenticated', 'public.source_record', 'raw_object_uri', 'SELECT'), 'no raw reference privilege');
select ok(not has_column_privilege('authenticated', 'public.source_record', 'vault_user_id', 'SELECT'), 'nor the recipient');

select tests.as_postgres();
select is((select body_clean from public.source_record where external_id = 'rec-1'), 'A body nobody may read here.', 'the service side still holds it');

select * from finish();
rollback;
