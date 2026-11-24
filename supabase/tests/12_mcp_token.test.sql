-- MCP personal access tokens: generated in the database, only the hash is stored, owner/admin only.
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as
select tests.create_user('owner@example.com') as owner_id,
       tests.create_user('admin@example.com') as admin_id,
       tests.create_user('other@example.com') as other_id;
grant select on fx to authenticated;
create temp table t (k text primary key, v text) on commit drop;
grant all on t to authenticated;

select tests.authenticate_as((select owner_id from fx));
insert into t select 'ws', public.create_workspace('A')::text;
insert into t select 'inv', token from public.create_workspace_invite((select v::uuid from t where k = 'ws'), 'admin@example.com');
select tests.authenticate_as((select admin_id from fx));
select public.accept_workspace_invite((select v from t where k = 'inv'));

-- creation
select tests.authenticate_as((select owner_id from fx));
insert into t select 'tok', token from public.create_mcp_token((select v::uuid from t where k = 'ws'), '  laptop ');
select ok((select v from t where k = 'tok') ~ '^frm_[0-9a-f]{64}$', 'token has the frm_ format and 256 bits');
select is((select name from public.mcp_token), 'laptop', 'name is trimmed');
select is((select token_prefix from public.mcp_token), substr((select v from t where k = 'tok'), 1, 12), 'prefix identifies the token');
select is((select scopes from public.mcp_token), array['read'], 'read is the only scope');
select ok((select expires_at from public.mcp_token) between now() + interval '89 days' and now() + interval '91 days', 'default lifetime is 90 days');
select is((select action from public.audit_log where action = 'mcp_token.created'), 'mcp_token.created', 'creation is audited');

select tests.as_postgres();
select is((select token_hash from public.mcp_token), sha256(convert_to((select v from t where k = 'tok'), 'UTF8')), 'only the sha256 of the secret is stored');
select tests.authenticate_as((select owner_id from fx));

-- the hash is not readable and users cannot write directly
select ok(not has_column_privilege('authenticated', 'public.mcp_token', 'token_hash', 'SELECT'), 'token hash is not readable by users');
select throws_ok(
  $$insert into public.mcp_token (workspace_id, user_id, name, token_hash, token_prefix, expires_at)
    values (gen_random_uuid(), gen_random_uuid(), 'x', '\x00', 'frm_00000000', now() + interval '1 day')$$,
  '42501', null, 'direct inserts are refused');

-- validation
select throws_ok(format($$select public.create_mcp_token(%L, 'x', 0)$$, (select v from t where k = 'ws')), '22023', 'invalid lifetime', 'lifetime below 1 day rejected');
select throws_ok(format($$select public.create_mcp_token(%L, 'x', 366)$$, (select v from t where k = 'ws')), '22023', 'invalid lifetime', 'lifetime above 365 days rejected');
select throws_ok(format($$select public.create_mcp_token(%L, '   ')$$, (select v from t where k = 'ws')), '23514', null, 'blank name rejected');

-- visibility: owner and admin of the workspace see tokens, others do not
select tests.authenticate_as((select admin_id from fx));
select is((select count(*)::int from public.mcp_token), 1, 'admins see the workspace tokens');
select tests.authenticate_as((select other_id from fx));
select is((select count(*)::int from public.mcp_token), 0, 'outsiders see no tokens');
select throws_ok(format($$select public.create_mcp_token(%L, 'x')$$, (select v from t where k = 'ws')), '42501', 'forbidden', 'outsiders cannot create tokens');

-- revocation
select tests.authenticate_as((select admin_id from fx));
select lives_ok(format($$select public.revoke_mcp_token(%L)$$, (select id from public.mcp_token limit 1)), 'admins can revoke');
select ok((select revoked_at is not null from public.mcp_token), 'token is revoked');
select is((select count(*)::int from public.audit_log where action = 'mcp_token.revoked'), 1, 'revocation is audited');
select tests.authenticate_as((select other_id from fx));
select throws_ok(format($$select public.revoke_mcp_token(%L)$$, gen_random_uuid()), '42501', 'forbidden', 'unknown token looks forbidden');

-- at most ten active tokens per user and workspace
select tests.authenticate_as((select admin_id from fx));
select public.create_mcp_token((select v::uuid from t where k = 'ws'), 'n' || g) from generate_series(1, 10) g;
select throws_ok(format($$select public.create_mcp_token(%L, 'eleventh')$$, (select v from t where k = 'ws')), '54000', 'too many active tokens', 'the eleventh active token is refused');

-- a token dies with the membership
select tests.authenticate_as((select owner_id from fx));
select public.remove_member((select v::uuid from t where k = 'ws'), (select admin_id from fx));
select tests.as_postgres();
select is((select count(*)::int from public.mcp_token where user_id = (select admin_id from fx)), 0, 'removing a member deletes their tokens');

select * from finish();
rollback;
