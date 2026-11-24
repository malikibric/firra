-- Structural guarantees that hold for every table and function, including future ones.
begin;
\ir _helpers.psql
select no_plan();

select ok(exists (select 1 from pg_tables where schemaname = 'public'), 'public schema has tables');

select ok(c.relrowsecurity, format('RLS enabled on public.%I', c.relname))
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public' and c.relkind in ('r', 'p');

select ok(
  exists (select 1 from information_schema.columns col
           where col.table_schema = 'public' and col.table_name = c.relname
             and col.column_name = 'workspace_id' and col.is_nullable = 'NO'),
  format('public.%I has workspace_id not null', c.relname))
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public' and c.relkind in ('r', 'p') and c.relname <> 'workspace';

select ok(
  (select count(*) from information_schema.columns col
    where col.table_schema = 'public' and col.table_name = c.relname
      and col.column_name in ('id', 'created_at', 'updated_at')) = 3,
  format('public.%I has id, created_at, updated_at', c.relname))
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public' and c.relkind in ('r', 'p');

select ok(
  exists (select 1 from pg_policy p where p.polrelid = c.oid),
  format('public.%I has at least one RLS policy', c.relname))
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public' and c.relkind in ('r', 'p');

select is_empty(
  $$ select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm')
        and has_table_privilege('anon', c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE') $$,
  'anon has no table privileges in public');

select is_empty(
  $$ select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm')
        and has_table_privilege('authenticated', c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE') $$,
  'authenticated has no direct write privileges in public');

select ok(
  not has_column_privilege('authenticated', 'public.workspace_invite', 'token_hash', 'SELECT'),
  'invite token hashes are not readable by users');

select ok(
  not has_column_privilege('authenticated', 'public.mcp_token', 'token_hash', 'SELECT'),
  'MCP token hashes are not readable by users');

select is_empty(
  $$ select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
      where n.nspname in ('public', 'private') and has_function_privilege('anon', p.oid, 'EXECUTE') $$,
  'anon cannot execute any public/private function');

select is_empty(
  $$ select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
      where n.nspname in ('public', 'private') and p.prosecdef
        and not exists (select 1 from unnest(p.proconfig) cfg where cfg like 'search_path=%') $$,
  'every SECURITY DEFINER function pins search_path');

select * from finish();
rollback;
