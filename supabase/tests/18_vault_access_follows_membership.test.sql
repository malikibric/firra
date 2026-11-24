-- Vault delivery follows the current relationship to the workspace; Full again allows text again.
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as
select tests.create_user('owner@example.com') as owner_id,
       tests.create_user('admin@example.com') as admin_id,
       tests.create_user('helper@example.com') as helper_id;
grant select on fx to authenticated;
create temp table t (k text primary key, v text) on commit drop;
grant all on t to authenticated;

select tests.authenticate_as((select owner_id from fx));
insert into t select 'ws', public.create_workspace('A')::text;
insert into t select 'client', public.create_client((select v::uuid from t where k = 'ws'), 'Acme')::text;
select tests.as_postgres();

insert into public.membership (workspace_id, user_id, role)
values ((select v::uuid from t where k = 'ws'), (select admin_id from fx), 'admin');
insert into public.contributor (workspace_id, email, status, user_id)
values ((select v::uuid from t where k = 'ws'), 'helper@example.com', 'active', (select helper_id from fx));
insert into public.connection (workspace_id, provider, mode, account_label, external_account_id, owner_user_id, status, token_ciphertext)
values ((select v::uuid from t where k = 'ws'), 'gmail', 'user', 'admin box', 'admin@example.com', (select admin_id from fx), 'active', '\x01');
insert into public.connection (workspace_id, provider, mode, account_label, external_account_id, owner_contributor_id, status, token_ciphertext)
values ((select v::uuid from t where k = 'ws'), 'gmail', 'user', 'helper box', 'helper@example.com',
        (select id from public.contributor where email = 'helper@example.com'), 'active', '\x02');
insert into public.connection (workspace_id, provider, mode, account_label, external_account_id, owner_user_id, status)
values ((select v::uuid from t where k = 'ws'), 'gmail', 'user', 'owner box', 'owner@example.com', (select owner_id from fx), 'active');

insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id,
                                  policy_at_ingest, occurred_at, direction, word_count, body_clean, vault_user_id)
select (select v::uuid from t where k = 'ws'), c.id, 'gmail', 'rec-' || c.account_label, 'email',
       (select v::uuid from t where k = 'client'), 'full', now(), 'in', 3, 'Some text here.',
       coalesce(c.owner_user_id, (select helper_id from fx))
  from public.connection c where c.workspace_id = (select v::uuid from t where k = 'ws');

-- Removing the admin ends their mailbox and their delivery, nobody else's.
select tests.authenticate_as((select owner_id from fx));
select lives_ok(format($$select public.remove_member(%L, %L)$$, (select v from t where k = 'ws'), (select admin_id from fx)),
  'the owner removes the admin');
select tests.as_postgres();
select is((select status::text from public.connection where account_label = 'admin box'), 'revoked', 'their mailbox is revoked');
select is((select token_ciphertext is null from public.connection where account_label = 'admin box'), true, 'its token is wiped');
select is((select count(*)::int from public.source_record where vault_user_id = (select admin_id from fx)), 0,
  'none of their mail is delivered to them any more');
select is((select vault_user_id from public.source_record where external_id = 'rec-owner box'), (select owner_id from fx),
  'the owner keeps their own delivery');
select is((select status::text from public.connection where account_label = 'owner box'), 'active', 'and their mailbox');

-- Revoking the contributor ends their delivery too.
select tests.authenticate_as((select owner_id from fx));
select lives_ok(format($$select public.revoke_contributor(%L)$$, (select id from public.contributor where email = 'helper@example.com')),
  'the owner revokes the contributor');
select tests.as_postgres();
select is((select count(*)::int from public.source_record where vault_user_id = (select helper_id from fx)), 0,
  'none of the contributor''s mail is delivered to them any more');

-- Full again: text that was stripped by the policy may be stored again; retention purges stay.
insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id,
                                  policy_at_ingest, occurred_at, direction, word_count, body_clean, body_purged_at)
values ((select v::uuid from t where k = 'ws'), (select id from public.connection where account_label = 'owner box'), 'gmail',
        'stripped', 'email', (select v::uuid from t where k = 'client'), 'metadata_only', now(), 'in', 3, null, now()),
       ((select v::uuid from t where k = 'ws'), (select id from public.connection where account_label = 'owner box'), 'gmail',
        'retained-out', 'email', (select v::uuid from t where k = 'client'), 'full', now(), 'in', 3, null, now());
select tests.authenticate_as((select owner_id from fx));
select lives_ok(format($$select public.set_client_policy(%L, 'metadata_only')$$, (select v from t where k = 'client')), 'metadata only');
select lives_ok(format($$select public.set_client_policy(%L, 'full')$$, (select v from t where k = 'client')), 'full again');
select tests.as_postgres();
select is((select body_purged_at from public.source_record where external_id = 'stripped'), null::timestamptz,
  'a record stored as metadata only may take its text again');
select ok((select count(*) from public.source_record
            where client_id = (select v::uuid from t where k = 'client') and policy_at_ingest = 'metadata_only'
              and body_purged_at is not null) = 0, 'no metadata-only record keeps a purge mark');

select * from finish();
rollback;
