-- audit_log is append-only and readable by owners/admins of the workspace only.
begin;
\ir _helpers.psql
select plan(4);

create temp table fx on commit drop as select tests.create_user('owner@example.com') as owner_id;
grant select on fx to authenticated;
select tests.authenticate_as((select owner_id from fx));
create temp table ws on commit drop as select public.create_workspace('A') as id;
grant select on ws to authenticated;

select is((select actor_type::text from public.audit_log), 'user', 'RPC audit rows record the user actor');
select is((select actor_id from public.audit_log), (select owner_id from fx), 'actor id is the caller');

select tests.as_postgres();
select throws_ok($$update public.audit_log set action = 'x.y'$$, 'P0001', 'audit_log is append-only',
  'updates are rejected even for privileged roles');
set local role service_role;
select throws_ok($$update public.audit_log set action = 'x.y'$$, 'P0001', 'audit_log is append-only',
  'service role cannot update audit rows');

select * from finish();
rollback;
