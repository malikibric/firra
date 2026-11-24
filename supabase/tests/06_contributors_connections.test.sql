-- Contributor links and connection secrecy.
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
insert into t select 'tok1', token from public.create_contributor_invite((select v::uuid from t where k = 'ws'), ' Dev@Firm.com ', 'Dev');
insert into t select 'cid', id::text from public.contributor;
select is((select email from public.contributor), 'dev@firm.com', 'contributor email normalized');
select is(length((select v from t where k = 'tok1')), 64, 'connect token is 256-bit');
select throws_ok('select token_hash from public.contributor', '42501', null, 'token hash not readable');

insert into t select 'tok2', token from public.create_contributor_invite((select v::uuid from t where k = 'ws'), 'dev@firm.com');
select is((select count(*)::int from public.contributor), 1, 're-invite reuses the contributor');
select ok((select v from t where k = 'tok1') <> (select v from t where k = 'tok2'), 're-invite rotates the token');

-- A connection owned by the contributor, with an encrypted token.
select tests.as_postgres();
insert into public.connection (workspace_id, provider, mode, owner_contributor_id, account_label, external_account_id, token_ciphertext)
select (select v::uuid from t where k = 'ws'), 'gmail', 'user', (select v::uuid from t where k = 'cid'), 'dev@firm.com', 'g-123', 'v1.x.y';
select tests.authenticate_as((select owner_id from fx));
select is((select account_label from public.connection), 'dev@firm.com', 'members see connections');
select throws_ok('select token_ciphertext from public.connection', '42501', null, 'tokens never readable by users');
select throws_ok('select wrapped_dek from public.workspace_key', '42501', null, 'workspace keys never readable by users');

select tests.authenticate_as((select other_id from fx));
select throws_ok(format('select public.revoke_contributor(%L)', (select v from t where k = 'cid')), '42501', 'forbidden',
  'outsiders cannot revoke');
select is((select count(*)::int from public.connection), 0, 'outsiders see no connections');

select tests.authenticate_as((select owner_id from fx));
select lives_ok(format('select public.revoke_contributor(%L)', (select v from t where k = 'cid')), 'owner revokes contributor');
select tests.as_postgres();
select is((select status::text from public.contributor), 'revoked', 'contributor revoked');
select ok((select token_hash is null from public.contributor), 'connect token invalidated');
select is((select status::text || ':' || coalesce(token_ciphertext, 'null') from public.connection), 'revoked:null',
  'contributor connections revoked and tokens wiped');

select * from finish();
rollback;
