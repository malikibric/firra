-- Tenancy: workspaces, owner/admin memberships, admin invitations.

create table public.workspace (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(btrim(name)) between 1 and 120),
  timezone text not null default 'UTC',
  default_locale public.app_locale not null default 'en',
  -- ISO weekday, 1 = Monday.
  brief_day smallint not null default 1 check (brief_day between 1 and 7),
  brief_hour smallint not null default 8 check (brief_hour between 0 and 23),
  llm_budget_usd_month numeric(10, 2) not null default 50 check (llm_budget_usd_month >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.membership (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  role public.member_role not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, user_id)
);
create index membership_user_id_idx on public.membership (user_id);

create table public.workspace_invite (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  email text not null check (email = lower(btrim(email)) and email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  role public.member_role not null default 'admin' check (role = 'admin'),
  -- sha256 of the one-time token; the plain token is only ever returned to the inviter once.
  token_hash bytea not null unique,
  invited_by uuid references auth.users (id) on delete set null,
  expires_at timestamptz not null,
  accepted_at timestamptz,
  accepted_by uuid references auth.users (id) on delete set null,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index workspace_invite_one_pending_per_email
  on public.workspace_invite (workspace_id, email)
  where accepted_at is null and revoked_at is null;

create trigger workspace_set_updated_at before update on public.workspace
  for each row execute function private.set_updated_at();
create trigger membership_set_updated_at before update on public.membership
  for each row execute function private.set_updated_at();
create trigger workspace_invite_set_updated_at before update on public.workspace_invite
  for each row execute function private.set_updated_at();

-- Membership predicates used by RLS. SECURITY DEFINER avoids recursive RLS on membership.
create function private.is_member(p_workspace_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.membership m
    where m.workspace_id = p_workspace_id
      and m.user_id = (select auth.uid())
  );
$$;

create function private.has_role(p_workspace_id uuid, p_roles public.member_role[])
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.membership m
    where m.workspace_id = p_workspace_id
      and m.user_id = (select auth.uid())
      and m.role = any (p_roles)
  );
$$;

grant execute on function private.is_member(uuid) to authenticated, service_role;
grant execute on function private.has_role(uuid, public.member_role[]) to authenticated, service_role;

-- A workspace must always keep at least one owner (except when the workspace itself is deleted).
create function private.ensure_workspace_keeps_owner()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if old.role = 'owner' and (tg_op = 'DELETE' or new.role <> 'owner') then
    -- Serialize concurrent owner removals for this workspace.
    perform 1 from public.workspace w where w.id = old.workspace_id for update;
    if found and not exists (
      select 1 from public.membership m
      where m.workspace_id = old.workspace_id and m.role = 'owner' and m.id <> old.id
    ) then
      raise exception 'a workspace must keep at least one owner'
        using errcode = 'P0001', hint = 'last_owner';
    end if;
  end if;
  return coalesce(new, old);
end;
$$;

create trigger membership_keep_owner before update or delete on public.membership
  for each row execute function private.ensure_workspace_keeps_owner();

-- RLS: members read their workspace; owners/admins read invites. No direct writes.
alter table public.workspace enable row level security;
alter table public.membership enable row level security;
alter table public.workspace_invite enable row level security;

create policy workspace_select_member on public.workspace
  for select to authenticated using (private.is_member(id));

create policy membership_select_member on public.membership
  for select to authenticated using (private.is_member(workspace_id));

create policy workspace_invite_select_admin on public.workspace_invite
  for select to authenticated
  using (private.has_role(workspace_id, array['owner', 'admin']::public.member_role[]));

grant select on public.workspace to authenticated;
grant select on public.membership to authenticated;
-- token_hash is deliberately not readable by users.
grant select (id, workspace_id, email, role, invited_by, expires_at, accepted_at, accepted_by,
              revoked_at, created_at, updated_at)
  on public.workspace_invite to authenticated;
