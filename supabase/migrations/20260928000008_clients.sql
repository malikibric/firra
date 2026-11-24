-- Clients of the firm, their email domains and data policy (full | metadata_only | excluded).
-- The policy is enforced at ingest (minimization) and at every read path.

create type public.client_status as enum ('active', 'churned', 'prospect');
create type public.data_policy as enum ('full', 'metadata_only', 'excluded');

alter table public.workspace
  add column internal_mentions boolean not null default false;

create table public.client (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  name text not null check (char_length(btrim(name)) between 1 and 200),
  status public.client_status not null default 'active',
  policy public.data_policy not null default 'full',
  mrr numeric(12, 2) check (mrr >= 0),
  currency char(3) check (currency ~ '^[A-Z]{3}$'),
  contract_start date,
  renewal_date date,
  notice_period_days integer check (notice_period_days >= 0),
  last_price_increase_at date,
  churned_at date,
  service_tags text[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- Target for composite foreign keys: children must belong to the same workspace.
  unique (workspace_id, id)
);
create index client_workspace_status_idx on public.client (workspace_id, status);

create table public.client_domain (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  client_id uuid not null,
  domain text not null check (domain = lower(domain)
    and domain ~ '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade,
  unique (workspace_id, domain)
);
create index client_domain_client_idx on public.client_domain (workspace_id, client_id);

create trigger client_set_updated_at before update on public.client
  for each row execute function private.set_updated_at();
create trigger client_domain_set_updated_at before update on public.client_domain
  for each row execute function private.set_updated_at();

alter table public.client enable row level security;
alter table public.client_domain enable row level security;

create policy client_select_member on public.client
  for select to authenticated using (private.is_member(workspace_id));
create policy client_domain_select_member on public.client_domain
  for select to authenticated using (private.is_member(workspace_id));

grant select on public.client, public.client_domain to authenticated;

-- Domains that must never be mapped to a client: shared mailbox providers.
create function private.is_public_email_domain(p_domain text)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select p_domain = any (array[
    'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com', 'msn.com',
    'yahoo.com', 'icloud.com', 'me.com', 'aol.com', 'proton.me', 'protonmail.com', 'gmx.com',
    'gmx.de', 'web.de', 'mail.com', 'yandex.com', 'zoho.com'
  ]);
$$;

-- Replaces a client's domains; raises on public, invalid or already-mapped domains.
-- Called from RPCs, so failures roll back the whole operation.
create function private.apply_client_domains(p_workspace_id uuid, p_client_id uuid, p_domains text[])
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_domains text[];
begin
  select coalesce(array_agg(distinct d), '{}') into v_domains
    from (select lower(btrim(x)) as d from unnest(coalesce(p_domains, '{}')) x) s
   where d <> '';

  if exists (select 1 from unnest(v_domains) d where private.is_public_email_domain(d)) then
    raise exception 'public email domains cannot identify a client' using errcode = '22023';
  end if;

  delete from public.client_domain
   where workspace_id = p_workspace_id and client_id = p_client_id and not (domain = any (v_domains));

  insert into public.client_domain (workspace_id, client_id, domain)
  select p_workspace_id, p_client_id, d from unnest(v_domains) d
  on conflict (workspace_id, domain) do nothing;

  if exists (
    select 1 from unnest(v_domains) d
    join public.client_domain cd on cd.workspace_id = p_workspace_id and cd.domain = d
    where cd.client_id <> p_client_id
  ) then
    raise exception 'domain already belongs to another client' using errcode = '23505';
  end if;
  return cardinality(v_domains);
end;
$$;

create function public.create_client(
  p_workspace_id uuid,
  p_name text,
  p_policy public.data_policy default 'full',
  p_status public.client_status default 'active',
  p_domains text[] default '{}'
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_client_id uuid;
begin
  perform private.require_uid();
  if not private.has_role(p_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  insert into public.client (workspace_id, name, policy, status)
  values (p_workspace_id, btrim(p_name), p_policy, p_status)
  returning id into v_client_id;

  perform private.apply_client_domains(p_workspace_id, v_client_id, p_domains);

  perform private.write_audit(p_workspace_id, 'client.created', 'client', v_client_id,
                              jsonb_build_object('policy', p_policy));
  return v_client_id;
end;
$$;

create function public.update_client(
  p_client_id uuid,
  p_name text,
  p_status public.client_status
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_workspace_id uuid;
begin
  perform private.require_uid();
  select workspace_id into v_workspace_id from public.client where id = p_client_id;
  if v_workspace_id is null
     or not private.has_role(v_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  update public.client
     set name = btrim(p_name),
         status = p_status,
         churned_at = case when p_status = 'churned' then coalesce(churned_at, current_date) end
   where id = p_client_id;

  perform private.write_audit(v_workspace_id, 'client.updated', 'client', p_client_id);
end;
$$;

-- Replaces the client's domains. A domain can belong to only one client per workspace.
create function public.set_client_domains(p_client_id uuid, p_domains text[])
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_workspace_id uuid;
  v_count integer;
begin
  perform private.require_uid();
  select workspace_id into v_workspace_id from public.client where id = p_client_id;
  if v_workspace_id is null
     or not private.has_role(v_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  v_count := private.apply_client_domains(v_workspace_id, p_client_id, p_domains);
  perform private.write_audit(v_workspace_id, 'client.domains_set', 'client', p_client_id,
                              jsonb_build_object('domain_count', v_count));
end;
$$;

grant execute on function public.create_client(uuid, text, public.data_policy, public.client_status, text[]) to authenticated;
grant execute on function public.update_client(uuid, text, public.client_status) to authenticated;
grant execute on function public.set_client_domains(uuid, text[]) to authenticated;
