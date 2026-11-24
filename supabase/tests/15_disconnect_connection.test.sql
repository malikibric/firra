-- disconnect_connection: owner/admin revoke exactly one connection of their workspace.
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as
select tests.create_user('owner@example.com') as owner_id, tests.create_user('other@example.com') as other_id;
grant select on fx to authenticated;
create temp table t (k text primary key, v text) on commit drop;
grant all on t to authenticated;

select tests.authenticate_as((select owner_id from fx));
insert into t select 'ws', public.create_workspace('A')::text;
select tests.as_postgres();
insert into public.connection (workspace_id, provider, mode, account_label, owner_user_id, status, external_account_id, token_ciphertext)
values ((select v::uuid from t where k = 'ws'), 'gmail', 'user', 'one', (select owner_id from fx), 'active', 'one@example.com', '\x01'),
       ((select v::uuid from t where k = 'ws'), 'gmail', 'user', 'two', (select owner_id from fx), 'active', 'two@example.com', '\x02');
insert into t select 'one', id::text from public.connection where account_label = 'one';

select tests.authenticate_as((select other_id from fx));
select throws_ok(format($$select public.disconnect_connection(%L)$$, (select v from t where k = 'one')), '42501', 'forbidden',
  'someone outside the workspace cannot disconnect');
select tests.authenticate_as((select owner_id from fx));
select lives_ok(format($$select public.disconnect_connection(%L)$$, (select v from t where k = 'one')), 'the owner disconnects one connection');
select tests.as_postgres();
select is((select status::text from public.connection where account_label = 'one'), 'revoked', 'it is revoked');
select is((select token_ciphertext is null from public.connection where account_label = 'one'), true, 'its token is wiped');
select is((select status::text from public.connection where account_label = 'two'), 'active', 'the other connection is untouched');
select ok(exists (select 1 from public.audit_log where action = 'connection.disconnected'), 'it is audited');
select tests.authenticate_as((select owner_id from fx));
select throws_ok($$select public.disconnect_connection(gen_random_uuid())$$, '42501', null, 'an unknown id looks forbidden');

select * from finish();
rollback;
