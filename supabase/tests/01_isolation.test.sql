-- Cross-workspace isolation: a user must never read another workspace's rows.
begin;
\ir _helpers.psql
select plan(26);

-- Fixtures: two owners, each with a workspace, a pending invite, a job, usage and audit rows.
create temp table fx on commit drop as
select tests.create_user('owner-a@example.com') as user_a,
       tests.create_user('owner-b@example.com') as user_b;
grant select on fx to authenticated, anon;

select tests.authenticate_as((select user_a from fx));
create temp table ws_a on commit drop as select public.create_workspace('Agency A') as id;
select public.create_workspace_invite((select id from ws_a), 'admin-a@example.com');
select tests.authenticate_as((select user_b from fx));
create temp table ws_b on commit drop as select public.create_workspace('Agency B') as id;
select public.create_workspace_invite((select id from ws_b), 'admin-b@example.com');
select tests.as_postgres();
grant select on ws_a, ws_b to authenticated, anon;

insert into public.job (workspace_id, type, idempotency_key)
select id, 'diagnostics.ping', 'k-' || id from (select id from ws_a union all select id from ws_b) w;
insert into public.llm_usage (workspace_id, model, input_tokens, output_tokens, cost_usd)
select id, 'test-model', 10, 5, 0.001 from (select id from ws_a union all select id from ws_b) w;

-- As owner A: sees own rows, none of B's.
select tests.authenticate_as((select user_a from fx));
select is((select count(*)::int from public.workspace), 1, 'A sees exactly one workspace');
select is((select id from public.workspace), (select id from ws_a), 'A sees workspace A');
select is((select count(*)::int from public.membership where workspace_id = (select id from ws_b)), 0, 'A cannot see B memberships');
select is((select count(*)::int from public.workspace_invite where workspace_id = (select id from ws_b)), 0, 'A cannot see B invites');
select is((select count(*)::int from public.audit_log where workspace_id = (select id from ws_b)), 0, 'A cannot see B audit log');
select is((select count(*)::int from public.job where workspace_id = (select id from ws_b)), 0, 'A cannot see B jobs');
select is((select count(*)::int from public.llm_usage where workspace_id = (select id from ws_b)), 0, 'A cannot see B llm usage');
select is((select count(*)::int from public.membership), 1, 'A sees own membership');
select is((select count(*)::int from public.workspace_invite), 1, 'A sees own invite');
select ok((select count(*)::int from public.audit_log) >= 2, 'A sees own audit log');
select is((select count(*)::int from public.job), 1, 'A sees own job');
select is((select count(*)::int from public.llm_usage), 1, 'A sees own llm usage');

-- As owner B: symmetric.
select tests.authenticate_as((select user_b from fx));
select is((select id from public.workspace), (select id from ws_b), 'B sees only workspace B');
select is((select count(*)::int from public.job where workspace_id = (select id from ws_a)), 0, 'B cannot see A jobs');
select is((select count(*)::int from public.audit_log where workspace_id = (select id from ws_a)), 0, 'B cannot see A audit log');

-- B cannot act on A through RPCs.
select throws_ok(
  format('select public.create_workspace_invite(%L, %L)', (select id from ws_a), 'x@example.com'),
  '42501', 'forbidden', 'B cannot invite into A');
select throws_ok(
  format('select public.remove_member(%L, %L)', (select id from ws_a), (select user_a from fx)),
  '42501', 'forbidden', 'B cannot remove members of A');

-- Users cannot write directly, even to their own workspace.
select throws_ok(
  format($$insert into public.job (workspace_id, type, idempotency_key) values (%L, 'x', 'y')$$, (select id from ws_b)),
  '42501', null, 'users cannot insert jobs');
select throws_ok(
  format($$update public.workspace set name = 'hijack' where id = %L$$, (select id from ws_b)),
  '42501', null, 'users cannot update workspaces directly');
select throws_ok(
  format($$insert into public.membership (workspace_id, user_id, role) values (%L, %L, 'owner')$$,
         (select id from ws_a), (select user_b from fx)),
  '42501', null, 'users cannot insert memberships');
select throws_ok(
  $$insert into public.audit_log (workspace_id, actor_type, action) select id, 'user', 'x.y' from public.workspace$$,
  '42501', null, 'users cannot write audit log');
select throws_ok(
  'select token_hash from public.workspace_invite', '42501', null, 'users cannot read invite token hashes');

-- A user without memberships sees nothing.
select tests.as_postgres();
create temp table outsider on commit drop as select tests.create_user('outsider@example.com') as id;
grant select on outsider to authenticated;
select tests.authenticate_as((select id from outsider));
select is((select count(*)::int from public.workspace), 0, 'outsider sees no workspaces');
select is((select count(*)::int from public.job), 0, 'outsider sees no jobs');

-- anon has no access at all.
select tests.as_anon();
select throws_ok('select * from public.workspace', '42501', null, 'anon cannot read workspaces');
select throws_ok($$select public.create_workspace('x')$$, '42501', null, 'anon cannot call RPCs');

select * from finish();
rollback;
