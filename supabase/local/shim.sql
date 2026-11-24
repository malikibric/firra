-- Local v1: the few parts of Supabase that supabase/migrations rely on, for the bundled PostgreSQL.
-- Applied once, before the migrations, by firra.local.db.migrate. The cluster must be created with superuser `postgres`
-- (the migrations alter default privileges "for role postgres"): initdb -U postgres.
--
-- What the migrations and the pgTAP helper use (checked 2026-10-05): auth.users (id, instance_id, aud, role, email,
-- email_confirmed_at, raw_app_meta_data, raw_user_meta_data, created_at, updated_at), auth.uid(),
-- roles anon/authenticated/service_role, extensions.gen_random_bytes, the vector extension in schema `extensions`.

create schema if not exists extensions;
create schema if not exists auth;

do $$
begin
  if not exists (select from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select from pg_roles where rolname = 'service_role') then create role service_role nologin bypassrls; end if;
end
$$;

grant usage on schema extensions to anon, authenticated, service_role;
grant usage on schema public to anon, authenticated, service_role;

-- Supabase's own setup lets service_role do everything in `public`; migration 1 then revokes the defaults from
-- anon and authenticated only. Objects created by the migrations (role postgres) therefore start with full
-- service_role access, which is what the engine's service-role code paths and the pgTAP suite rely on.
alter default privileges for role postgres in schema public grant all on tables to service_role;
alter default privileges for role postgres in schema public grant all on sequences to service_role;
alter default privileges for role postgres in schema public grant all on functions to service_role;
grant usage on schema auth to anon, authenticated, service_role;

-- The local owner (and nobody else) lives here. firra.local.bootstrap inserts the row (L1-03).
create table if not exists auth.users (
  id uuid primary key default gen_random_uuid(),
  instance_id uuid,
  aud text,
  role text,
  email text,
  email_confirmed_at timestamptz,
  raw_app_meta_data jsonb not null default '{}'::jsonb,
  raw_user_meta_data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
revoke all on auth.users from anon, authenticated;

-- Supabase reads the caller from the request's JWT claims; the engine sets request.jwt.claim.sub per transaction
-- (select set_config('request.jwt.claim.sub', '<user id>', true)) and switches to role authenticated for caller-style calls.
create or replace function auth.uid()
returns uuid
language sql
stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    nullif(current_setting('request.jwt.claims', true)::jsonb ->> 'sub', '')
  )::uuid
$$;

-- pgcrypto is not bundled; the migrations only need random bytes (tokens whose hash is stored). gen_random_uuid() is
-- backed by the server's strong random source.
create or replace function extensions.gen_random_bytes(n integer)
returns bytea
language plpgsql
volatile
as $$
declare
  out bytea := ''::bytea;
begin
  if n < 1 or n > 1024 then
    raise exception 'gen_random_bytes: length must be between 1 and 1024' using errcode = '22023';
  end if;
  while length(out) < n loop
    out := out || decode(replace(gen_random_uuid()::text, '-', ''), 'hex');
  end loop;
  return substring(out from 1 for n);
end
$$;
