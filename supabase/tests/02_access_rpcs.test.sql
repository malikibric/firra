-- Workspace creation, admin invitations, member removal and the last-owner rule.
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as
select tests.create_user('owner@example.com') as owner_id,
       tests.create_user('admin@example.com') as admin_id,
       tests.create_user('other@example.com') as other_id,
       tests.create_user('unconfirmed@example.com', false) as unconfirmed_id;
grant select on fx to authenticated;
create temp table t (k text primary key, v text) on commit drop;
grant all on t to authenticated;

-- create_workspace
select tests.authenticate_as((select owner_id from fx));
insert into t select 'ws', public.create_workspace('  Northbeam  ', 'Europe/Berlin', 'de')::text;
select is((select name from public.workspace), 'Northbeam', 'workspace name is trimmed');
select is((select timezone from public.workspace), 'Europe/Berlin', 'timezone stored');
select is((select role::text from public.membership where user_id = (select owner_id from fx)), 'owner', 'creator becomes owner');
select is((select action from public.audit_log order by created_at limit 1), 'workspace.created', 'creation is audited');
select throws_ok($$select public.create_workspace('x', 'Mars/Olympus')$$, '22023', 'unknown timezone', 'invalid timezone rejected');
select throws_ok($$select public.create_workspace('   ')$$, '23514', null, 'blank name rejected');

-- create_workspace_invite
insert into t select 'tok1', token from public.create_workspace_invite((select v::uuid from t where k = 'ws'), ' Admin@Example.com ');
select is((select email from public.workspace_invite), 'admin@example.com', 'invite email normalized');
select is((select role::text from public.workspace_invite), 'admin', 'invite role is admin');
select is(length((select v from t where k = 'tok1')), 64, 'token is 256-bit hex');
select throws_ok(
  format('select public.create_workspace_invite(%L, %L)', (select v from t where k = 'ws'), 'owner@example.com'),
  '23505', 'already a member', 'cannot invite an existing member');

-- Re-inviting revokes the previous pending invite.
insert into t select 'tok2', token from public.create_workspace_invite((select v::uuid from t where k = 'ws'), 'admin@example.com');
select is((select count(*)::int from public.workspace_invite where revoked_at is null), 1, 're-invite leaves one pending invite');

-- accept_workspace_invite: wrong user, unconfirmed user, revoked token, then success.
select tests.authenticate_as((select other_id from fx));
select throws_ok(format('select public.accept_workspace_invite(%L)', (select v from t where k = 'tok2')),
  '42501', 'invite was issued for a different email', 'other email cannot accept');
select throws_ok($$select public.accept_workspace_invite('nope')$$,
  'P0002', 'invite is invalid or expired', 'unknown token rejected');
select tests.authenticate_as((select admin_id from fx));
select throws_ok(format('select public.accept_workspace_invite(%L)', (select v from t where k = 'tok1')),
  'P0002', 'invite is invalid or expired', 'revoked token rejected');
select lives_ok(format('select public.accept_workspace_invite(%L)', (select v from t where k = 'tok2')),
  'invited admin accepts');
select is((select role::text from public.membership where user_id = (select admin_id from fx)), 'admin', 'admin membership created');
select is((select count(*)::int from public.workspace), 1, 'admin now sees the workspace');
select throws_ok(format('select public.accept_workspace_invite(%L)', (select v from t where k = 'tok2')),
  'P0002', 'invite is invalid or expired', 'token cannot be reused');

-- Admins can invite admins; unconfirmed emails cannot accept.
insert into t select 'tok3', token from public.create_workspace_invite((select v::uuid from t where k = 'ws'), 'unconfirmed@example.com');
select tests.authenticate_as((select unconfirmed_id from fx));
select throws_ok(format('select public.accept_workspace_invite(%L)', (select v from t where k = 'tok3')),
  '42501', 'invite was issued for a different email', 'unconfirmed email cannot accept');

-- Expired invites cannot be accepted.
select tests.as_postgres();
update public.workspace_invite set expires_at = now() - interval '1 minute' where revoked_at is null and accepted_at is null;
update auth.users set email_confirmed_at = now() where id = (select unconfirmed_id from fx);
select tests.authenticate_as((select unconfirmed_id from fx));
select throws_ok(format('select public.accept_workspace_invite(%L)', (select v from t where k = 'tok3')),
  'P0002', 'invite is invalid or expired', 'expired invite rejected');

-- remove_member: admins cannot remove, owners can; the last owner cannot be removed.
select tests.authenticate_as((select admin_id from fx));
select throws_ok(format('select public.remove_member(%L, %L)', (select v from t where k = 'ws'), (select owner_id from fx)),
  '42501', 'forbidden', 'admin cannot remove members');
select tests.authenticate_as((select owner_id from fx));
select throws_ok(format('select public.remove_member(%L, %L)', (select v from t where k = 'ws'), (select owner_id from fx)),
  'P0001', 'a workspace must keep at least one owner', 'last owner cannot be removed');
select lives_ok(format('select public.remove_member(%L, %L)', (select v from t where k = 'ws'), (select admin_id from fx)),
  'owner removes admin');
select is((select count(*)::int from public.membership), 1, 'admin membership removed');
select ok(exists (select 1 from public.audit_log where action = 'membership.removed'), 'removal is audited');

-- revoke_workspace_invite
insert into t select 'tok4', token from public.create_workspace_invite((select v::uuid from t where k = 'ws'), 'late@example.com');
select lives_ok(format('select public.revoke_workspace_invite(%L)',
  (select id from public.workspace_invite where email = 'late@example.com')), 'owner revokes invite');
select ok((select revoked_at is not null from public.workspace_invite where email = 'late@example.com'), 'invite revoked');

-- Deleting the workspace itself is not blocked by the last-owner rule.
select tests.as_postgres();
select lives_ok(format('delete from public.workspace where id = %L', (select v from t where k = 'ws')),
  'workspace delete cascades memberships');

select * from finish();
rollback;
