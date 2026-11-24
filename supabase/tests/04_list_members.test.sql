begin;
\ir _helpers.psql
select plan(3);

create temp table fx on commit drop as
select tests.create_user('owner@example.com') as a, tests.create_user('other@example.com') as b;
grant select on fx to authenticated;
select tests.authenticate_as((select a from fx));
create temp table ws on commit drop as select public.create_workspace('A') as id;
grant select on ws to authenticated, anon;

select results_eq(
  $$ select email, role::text from public.list_workspace_members((select id from ws)) $$,
  $$ values ('owner@example.com', 'owner') $$,
  'members see member emails and roles');

select tests.authenticate_as((select b from fx));
select throws_ok(format('select * from public.list_workspace_members(%L)', (select id from ws)),
  '42501', 'forbidden', 'non-members cannot list members');

select tests.as_anon();
select throws_ok(format('select * from public.list_workspace_members(%L)', (select id from ws)),
  '42501', null, 'anon cannot call it');

select * from finish();
rollback;
