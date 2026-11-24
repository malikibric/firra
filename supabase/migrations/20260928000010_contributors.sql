-- Contributors: team members who only connect their own mailbox/calendar through a token link.
-- They have no Supabase account and no app access.

create type public.contributor_status as enum ('invited', 'active', 'revoked');

create table public.contributor (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  email text not null check (email = lower(btrim(email)) and email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  name text check (char_length(name) <= 200),
  status public.contributor_status not null default 'invited',
  -- sha256 of the connect-link token; null once revoked.
  token_hash bytea unique,
  invited_by uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, email),
  unique (workspace_id, id)
);

create trigger contributor_set_updated_at before update on public.contributor
  for each row execute function private.set_updated_at();

alter table public.contributor enable row level security;

create policy contributor_select_admin on public.contributor
  for select to authenticated
  using (private.has_role(workspace_id, array['owner', 'admin']::public.member_role[]));

grant select (id, workspace_id, email, name, status, invited_by, created_at, updated_at)
  on public.contributor to authenticated;

-- Creates (or re-issues) a contributor's connect link. Returns the plain token once.
create function public.create_contributor_invite(p_workspace_id uuid, p_email text, p_name text default null)
returns table (contributor_id uuid, token text)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := private.require_uid();
  v_token text := encode(extensions.gen_random_bytes(32), 'hex');
  v_id uuid;
begin
  if not private.has_role(p_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  insert into public.contributor (workspace_id, email, name, status, token_hash, invited_by)
  values (p_workspace_id, lower(btrim(p_email)), nullif(btrim(p_name), ''), 'invited',
          sha256(convert_to(v_token, 'UTF8')), v_uid)
  on conflict (workspace_id, email) do update
     set token_hash = excluded.token_hash,
         name = coalesce(excluded.name, public.contributor.name),
         status = case when public.contributor.status = 'revoked' then 'invited'
                       else public.contributor.status end,
         invited_by = excluded.invited_by
  returning id into v_id;

  perform private.write_audit(p_workspace_id, 'contributor.invited', 'contributor', v_id);
  contributor_id := v_id;
  token := v_token;
  return next;
end;
$$;

grant execute on function public.create_contributor_invite(uuid, text, text) to authenticated;
