-- oauth_state.return_to accepts site paths and the app address, nothing else.
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as select tests.create_user('owner@example.com') as owner_id;
select tests.authenticate_as((select owner_id from fx));
create temp table t (k text primary key, v text) on commit drop;
grant all on t to authenticated;
insert into t select 'ws', public.create_workspace('A')::text;
select tests.as_postgres();

create function pg_temp.try_return_to(p_return_to text) returns void language plpgsql as $$
begin
  insert into public.oauth_state (workspace_id, state_hash, provider, owner_user_id, code_verifier, return_to)
  values ((select v::uuid from t where k = 'ws'), sha256(convert_to(gen_random_uuid()::text, 'UTF8')), 'google',
          (select owner_id from fx), repeat('a', 43), p_return_to);
end $$;

select lives_ok($$select pg_temp.try_return_to('/en/w/x/connections')$$, 'a site path is accepted');
select lives_ok($$select pg_temp.try_return_to('firra://connected')$$, 'the app address is accepted');
select lives_ok($$select pg_temp.try_return_to('firra://connected/mail-ok')$$, 'with a plain sub path');
select throws_ok($$select pg_temp.try_return_to('https://evil.example/x')$$, '23514', null, 'an absolute web address is refused');
select throws_ok($$select pg_temp.try_return_to('//evil.example')$$, '23514', null, 'a protocol-relative address is refused');
select throws_ok($$select pg_temp.try_return_to('firra://Connected')$$, '23514', null, 'uppercase is refused');
select throws_ok($$select pg_temp.try_return_to('firra://connected?x=1')$$, '23514', null, 'a query is refused');
select throws_ok($$select pg_temp.try_return_to('javascript:alert(1)')$$, '23514', null, 'another scheme is refused');
select throws_ok($$select pg_temp.try_return_to('firra://')$$, '23514', null, 'an empty app address is refused');

select * from finish();
rollback;
