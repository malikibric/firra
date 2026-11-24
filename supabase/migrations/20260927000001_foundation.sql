-- Foundation: private helper schema, shared trigger, enums, least-privilege defaults.
--
-- Access model:
--   * anon: no access to anything in public.
--   * authenticated: SELECT only where granted per table, always filtered by RLS;
--     all writes go through SECURITY DEFINER RPCs.
--   * service_role (apps/api, services/worker only): full access, bypasses RLS.

create schema if not exists private;
revoke all on schema private from public;
-- RLS policies run as the calling role and call helpers in this schema.
grant usage on schema private to authenticated, service_role;

alter default privileges for role postgres in schema public revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public revoke execute on functions from public, anon, authenticated;
-- Postgres grants EXECUTE to PUBLIC on every new function by default; per-schema defaults cannot
-- remove that, so revoke it globally. Functions are callable only where granted explicitly.
alter default privileges for role postgres revoke execute on functions from public;

create function private.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

create type public.member_role as enum ('owner', 'admin');
create type public.app_locale as enum ('en', 'de', 'sv', 'fr', 'it');
create type public.actor_type as enum ('user', 'mcp', 'system');
create type public.job_status as enum ('queued', 'running', 'succeeded', 'failed');
