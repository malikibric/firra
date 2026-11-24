-- Client setup RPCs, domain rules and cross-tenant integrity.
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
insert into t select 'acme', public.create_client((select v from t where k = 'ws_a'), 'Acme Corp');
select tests.authenticate_as((select b from fx));
insert into t select 'ws_b', public.create_workspace('B');
insert into t select 'globex', public.create_client((select v from t where k = 'ws_b'), 'Globex');

select tests.authenticate_as((select a from fx));
select is((select policy::text from public.client where id = (select v from t where k = 'acme')), 'full', 'default policy is full');
select lives_ok(format($$select public.set_client_domains(%L, array[' ACME.com ', 'acme.io', 'acme.io'])$$, (select v from t where k = 'acme')),
  'owner sets domains');
select results_eq($$select domain from public.client_domain order by domain$$, $$values ('acme.com'), ('acme.io')$$,
  'domains are normalized and de-duplicated');
select throws_ok(format($$select public.set_client_domains(%L, array['gmail.com'])$$, (select v from t where k = 'acme')),
  '22023', 'public email domains cannot identify a client', 'public mailbox domains rejected');
select throws_ok(format($$select public.set_client_domains(%L, array['not a domain'])$$, (select v from t where k = 'acme')),
  '23514', null, 'invalid domains rejected');
insert into t select 'initech', public.create_client((select v from t where k = 'ws_a'), 'Initech');
select throws_ok(format($$select public.set_client_domains(%L, array['acme.com'])$$, (select v from t where k = 'initech')),
  '23505', 'domain already belongs to another client', 'a domain maps to one client only');
select lives_ok(format($$select public.set_client_domains(%L, array['acme.com'])$$, (select v from t where k = 'acme')),
  'replacing the set removes dropped domains');
select is((select count(*)::int from public.client_domain where client_id = (select v from t where k = 'acme')), 1, 'acme.io removed');

-- Creating a client with a rejected domain leaves nothing behind.
select throws_ok(format($$select public.create_client(%L, 'Hooli', 'full', 'active', array['gmail.com'])$$, (select v from t where k = 'ws_a')),
  '22023', 'public email domains cannot identify a client', 'create with a public domain fails');
select is((select count(*)::int from public.client where name = 'Hooli'), 0, 'failed create leaves no client');
insert into t select 'hooli', public.create_client((select v from t where k = 'ws_a'), 'Hooli', 'full', 'active', array['Hooli.XYZ']);
select is((select domain from public.client_domain where client_id = (select v from t where k = 'hooli')), 'hooli.xyz', 'create sets domains atomically');

-- Tenant isolation and RPC authorization.
select is((select count(*)::int from public.client where workspace_id = (select v from t where k = 'ws_b')), 0, 'A cannot see B clients');
select throws_ok(format($$select public.set_client_domains(%L, array['globex.com'])$$, (select v from t where k = 'globex')),
  '42501', 'forbidden', 'A cannot set B domains');
select throws_ok(format($$select public.create_client(%L, 'x')$$, (select v from t where k = 'ws_b')),
  '42501', 'forbidden', 'A cannot create clients in B');
select throws_ok(format($$select public.set_client_policy(%L, 'excluded')$$, (select v from t where k = 'globex')),
  '42501', 'forbidden', 'A cannot change B policies');

-- Composite foreign keys make cross-tenant links impossible even for the service role.
select tests.as_postgres();
select throws_ok(
  format($$insert into public.client_domain (workspace_id, client_id, domain) values (%L, %L, 'evil.com')$$,
         (select v from t where k = 'ws_a'), (select v from t where k = 'globex')),
  '23503', null, 'a domain cannot point at another workspace''s client');

-- acme, globex, initech, hooli created; acme domains set twice (failed attempts roll back).
select is((select count(*)::int from public.audit_log where action like 'client.%'
                 and workspace_id in ((select v from t where k = 'ws_a'), (select v from t where k = 'ws_b'))),
  6, 'successful client changes are audited');

select * from finish();
rollback;
