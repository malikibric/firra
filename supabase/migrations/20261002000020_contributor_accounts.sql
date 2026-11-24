-- Contributor accounts (docs/plans/d-mac-app.md, D-D3). A contributor signs in to the Mac app with
-- their own account and can connect and disconnect their own sources, nothing else. They are not a
-- `membership` row, so every `is_member` policy keeps hiding workspace data from them: access is
-- granted by the narrow policies below, never by widening existing ones.

alter table public.contributor
  add column user_id uuid references auth.users (id) on delete set null;
create unique index contributor_workspace_user_key
  on public.contributor (workspace_id, user_id) where user_id is not null;

-- Owners and admins see who claimed an invite; a contributor reads only their own row.
grant select (user_id) on public.contributor to authenticated;
create policy contributor_select_self on public.contributor
  for select to authenticated
  using (user_id = (select auth.uid()) and status <> 'revoked');

-- True when the caller is the active contributor with this id (used instead of a subquery inside the
-- connection policy so the check does not depend on the caller's own RLS on `contributor`).
create function private.is_active_contributor(p_workspace_id uuid, p_contributor_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.contributor c
    where c.workspace_id = p_workspace_id and c.id = p_contributor_id
      and c.user_id = (select auth.uid()) and c.status = 'active'
  );
$$;
grant execute on function private.is_active_contributor(uuid, uuid) to authenticated, service_role;

create policy connection_select_own_contributor on public.connection
  for select to authenticated
  using (owner_contributor_id is not null
         and private.is_active_contributor(workspace_id, owner_contributor_id));

-- Claims an invite for the signed-in user. The invite code is the connect-link token; the account's
-- confirmed email must be the invited address. The code stays valid until the contributor is revoked
-- (the transitional web link uses it too), but only that user can claim it.
create function public.claim_contributor_invite(p_token text)
returns table (out_workspace_id uuid, out_contributor_id uuid)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := private.require_uid();
  v_row public.contributor;
  v_email text;
begin
  if p_token is null or p_token !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid invite' using errcode = '22023';
  end if;
  select * into v_row from public.contributor c
   where c.token_hash = sha256(convert_to(p_token, 'UTF8')) and c.status in ('invited', 'active')
   for update;
  if not found then
    raise exception 'invalid invite' using errcode = 'P0002';
  end if;
  select lower(u.email) into v_email from auth.users u
   where u.id = v_uid and u.email_confirmed_at is not null;
  if v_email is distinct from v_row.email or (v_row.user_id is not null and v_row.user_id <> v_uid) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  update public.contributor set user_id = v_uid, status = 'active' where id = v_row.id;
  perform private.write_audit(v_row.workspace_id, 'contributor.claimed', 'contributor', v_row.id);
  out_workspace_id := v_row.workspace_id;
  out_contributor_id := v_row.id;
  return next;
end;
$$;

-- What the signed-in user is a contributor of (a contributor cannot read `workspace` directly).
create function public.my_contributions()
returns table (workspace_id uuid, workspace_name text, contributor_id uuid, status public.contributor_status)
language sql
stable
security definer
set search_path = ''
as $$
  select c.workspace_id, w.name, c.id, c.status
    from public.contributor c join public.workspace w on w.id = c.workspace_id
   where c.user_id = (select auth.uid()) and c.status = 'active';
$$;

-- A contributor ends one of their own connections and wipes its stored token, like an admin
-- revoking them does for all of them.
create function public.disconnect_own_connection(p_connection_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_workspace_id uuid;
  v_contributor_id uuid;
begin
  perform private.require_uid();
  select c.workspace_id, c.owner_contributor_id into v_workspace_id, v_contributor_id
    from public.connection c where c.id = p_connection_id;
  if v_workspace_id is null or v_contributor_id is null
     or not private.is_active_contributor(v_workspace_id, v_contributor_id) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  update public.connection set status = 'revoked', token_ciphertext = null where id = p_connection_id;
  perform private.write_audit(v_workspace_id, 'connection.disconnected_by_contributor', 'connection',
                              p_connection_id);
end;
$$;

grant execute on function public.claim_contributor_invite(text) to authenticated;
grant execute on function public.my_contributions() to authenticated;
grant execute on function public.disconnect_own_connection(uuid) to authenticated;
