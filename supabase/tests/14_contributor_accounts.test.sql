-- Contributor accounts: claim an invite, see only their own connections, nothing else, until revoked.
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as
select tests.create_user('owner@example.com') as owner_id,
       tests.create_user('contrib@example.com') as contrib_id,
       tests.create_user('other@example.com') as other_id;
grant select on fx to authenticated;
create temp table t (k text primary key, v text) on commit drop;
grant all on t to authenticated;

select tests.authenticate_as((select owner_id from fx));
insert into t select 'ws', public.create_workspace('A')::text;
insert into t select 'tok', token from public.create_contributor_invite((select v::uuid from t where k = 'ws'), 'contrib@example.com', 'Cee');
insert into t select 'client', public.create_client((select v::uuid from t where k = 'ws'), 'Acme')::text;

select tests.as_postgres();
insert into public.connection (workspace_id, provider, mode, account_label, owner_user_id, status, external_account_id)
values ((select v::uuid from t where k = 'ws'), 'gmail', 'user', 'owner-box', (select owner_id from fx), 'active', 'owner@example.com');
insert into public.connection (workspace_id, provider, mode, account_label, owner_contributor_id, status, external_account_id,
                               token_ciphertext)
values ((select v::uuid from t where k = 'ws'), 'gmail', 'user', 'contrib-box',
        (select id from public.contributor where email = 'contrib@example.com'), 'active', 'contrib@example.com', '\x01');

insert into t select 'owner_conn', id::text from public.connection where account_label = 'owner-box';

-- claiming
select tests.authenticate_as((select other_id from fx));
select throws_ok(format($$select public.claim_contributor_invite(%L)$$, (select v from t where k = 'tok')), '42501', 'forbidden',
  'a user whose email is not the invited one cannot claim');
select throws_ok($$select public.claim_contributor_invite('not-a-token')$$, '22023', 'invalid invite', 'a malformed code is refused');
select tests.authenticate_as((select contrib_id from fx));
select lives_ok(format($$select public.claim_contributor_invite(%L)$$, (select v from t where k = 'tok')), 'the invited user claims');
select lives_ok(format($$select public.claim_contributor_invite(%L)$$, (select v from t where k = 'tok')), 'claiming again is harmless');
select tests.as_postgres();
select is((select status::text from public.contributor where email = 'contrib@example.com'), 'active', 'the contributor is active');
select is((select user_id from public.contributor where email = 'contrib@example.com'), (select contrib_id from fx), 'linked to the account');
select ok(exists (select 1 from public.audit_log where action = 'contributor.claimed'), 'claiming is audited');

-- what a contributor sees: their own connection and nothing about the workspace
select tests.authenticate_as((select contrib_id from fx));
select is((select count(*)::int from public.connection), 1, 'sees one connection');
select is((select account_label from public.connection), 'contrib-box', 'their own');
select is((select count(*)::int from public.workspace), 0, 'no workspace row');
select is((select count(*)::int from public.client), 0, 'no clients');
select is((select count(*)::int from public.membership), 0, 'no memberships');
select is((select count(*)::int from public.audit_log), 0, 'no audit log');
select is((select count(*)::int from public.mcp_token), 0, 'no MCP tokens');
select is((select count(*)::int from public.contributor), 1, 'their own contributor row only');
select ok(not has_column_privilege('authenticated', 'public.contributor', 'token_hash', 'SELECT'), 'the invite code hash is not readable');
select is((select workspace_name from public.my_contributions()), 'A', 'my_contributions names the workspace');
select throws_ok(format($$select public.create_client(%L, 'Nope')$$, (select v from t where k = 'ws')), '42501', null,
  'a contributor cannot create clients');
select throws_ok(format($$select public.create_contributor_invite(%L, 'x@example.com')$$, (select v from t where k = 'ws')), '42501', null,
  'a contributor cannot invite');

-- disconnecting: own only
select tests.authenticate_as((select contrib_id from fx));
select throws_ok(format($$select public.disconnect_own_connection(%L)$$, (select v from t where k = 'owner_conn')),
  '42501', null, 'a contributor cannot disconnect someone else''s connection');
select lives_ok(format($$select public.disconnect_own_connection(%L)$$, (select id from public.connection where account_label = 'contrib-box')),
  'a contributor disconnects their own connection');
select tests.as_postgres();
select is((select status::text from public.connection where account_label = 'contrib-box'), 'revoked', 'the connection is revoked');
select is((select token_ciphertext is null from public.connection where account_label = 'contrib-box'), true, 'and its token is wiped');
select is((select status::text from public.connection where account_label = 'owner-box'), 'active', 'the owner''s connection is untouched');

-- revoking the contributor ends everything
select tests.authenticate_as((select owner_id from fx));
select public.revoke_contributor((select id from public.contributor where email = 'contrib@example.com'));
select tests.authenticate_as((select contrib_id from fx));
select is((select count(*)::int from public.contributor), 0, 'a revoked contributor sees no row');
select is((select count(*)::int from public.connection), 0, 'and no connection');
select is((select count(*)::int from public.my_contributions()), 0, 'and no contributions');
select throws_ok(format($$select public.claim_contributor_invite(%L)$$, (select v from t where k = 'tok')), 'P0002', 'invalid invite',
  'the old code no longer claims');

select * from finish();
rollback;
