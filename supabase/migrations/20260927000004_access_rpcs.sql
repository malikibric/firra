-- Access RPCs: the only write paths for workspaces, memberships and invites from user sessions.
-- All are SECURITY DEFINER with an empty search_path and check the caller explicitly.

create function private.require_uid()
returns uuid
language plpgsql
stable
set search_path = ''
as $$
declare
  v_uid uuid := auth.uid();
begin
  if v_uid is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  return v_uid;
end;
$$;

create function public.create_workspace(
  p_name text,
  p_timezone text default 'UTC',
  p_default_locale public.app_locale default 'en'
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := private.require_uid();
  v_workspace_id uuid;
begin
  if not exists (select 1 from pg_catalog.pg_timezone_names where name = p_timezone) then
    raise exception 'unknown timezone' using errcode = '22023';
  end if;

  insert into public.workspace (name, timezone, default_locale)
  values (btrim(p_name), p_timezone, p_default_locale)
  returning id into v_workspace_id;

  insert into public.membership (workspace_id, user_id, role)
  values (v_workspace_id, v_uid, 'owner');

  perform private.write_audit(v_workspace_id, 'workspace.created', 'workspace', v_workspace_id);
  return v_workspace_id;
end;
$$;

create function public.create_workspace_invite(p_workspace_id uuid, p_email text)
returns table (invite_id uuid, token text, expires_at timestamptz)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := private.require_uid();
  v_email text := lower(btrim(p_email));
  v_token text := encode(extensions.gen_random_bytes(32), 'hex');
  v_invite public.workspace_invite;
begin
  if not private.has_role(p_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  if exists (
    select 1 from public.membership m join auth.users u on u.id = m.user_id
    where m.workspace_id = p_workspace_id and lower(u.email) = v_email
  ) then
    raise exception 'already a member' using errcode = '23505';
  end if;

  -- A new invite replaces any pending one for the same email.
  update public.workspace_invite i
     set revoked_at = now()
   where i.workspace_id = p_workspace_id and i.email = v_email
     and i.accepted_at is null and i.revoked_at is null;

  insert into public.workspace_invite (workspace_id, email, role, token_hash, invited_by, expires_at)
  values (p_workspace_id, v_email, 'admin', sha256(convert_to(v_token, 'UTF8')), v_uid,
          now() + interval '7 days')
  returning * into v_invite;

  perform private.write_audit(p_workspace_id, 'workspace_invite.created', 'workspace_invite', v_invite.id);

  invite_id := v_invite.id;
  token := v_token;
  expires_at := v_invite.expires_at;
  return next;
end;
$$;

create function public.accept_workspace_invite(p_token text)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := private.require_uid();
  v_user auth.users;
  v_invite public.workspace_invite;
begin
  select * into v_user from auth.users where id = v_uid;

  select * into v_invite from public.workspace_invite
   where token_hash = sha256(convert_to(coalesce(p_token, ''), 'UTF8'))
   for update;

  if not found or v_invite.revoked_at is not null or v_invite.accepted_at is not null
     or v_invite.expires_at <= now() then
    raise exception 'invite is invalid or expired' using errcode = 'P0002';
  end if;

  if v_user.email_confirmed_at is null or lower(v_user.email) <> v_invite.email then
    raise exception 'invite was issued for a different email' using errcode = '42501';
  end if;

  insert into public.membership (workspace_id, user_id, role)
  values (v_invite.workspace_id, v_uid, v_invite.role)
  on conflict (workspace_id, user_id) do nothing;

  update public.workspace_invite
     set accepted_at = now(), accepted_by = v_uid
   where id = v_invite.id;

  perform private.write_audit(v_invite.workspace_id, 'workspace_invite.accepted', 'workspace_invite', v_invite.id);
  return v_invite.workspace_id;
end;
$$;

create function public.revoke_workspace_invite(p_invite_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_workspace_id uuid;
begin
  perform private.require_uid();

  select workspace_id into v_workspace_id from public.workspace_invite where id = p_invite_id;
  if v_workspace_id is null
     or not private.has_role(v_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  update public.workspace_invite
     set revoked_at = now()
   where id = p_invite_id and accepted_at is null and revoked_at is null;

  perform private.write_audit(v_workspace_id, 'workspace_invite.revoked', 'workspace_invite', p_invite_id);
end;
$$;

create function public.remove_member(p_workspace_id uuid, p_user_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_membership_id uuid;
begin
  perform private.require_uid();

  if not private.has_role(p_workspace_id, array['owner']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  delete from public.membership
   where workspace_id = p_workspace_id and user_id = p_user_id
  returning id into v_membership_id;

  if v_membership_id is null then
    raise exception 'member not found' using errcode = 'P0002';
  end if;

  perform private.write_audit(p_workspace_id, 'membership.removed', 'membership', v_membership_id,
                              jsonb_build_object('user_id', p_user_id));
end;
$$;

grant execute on function public.create_workspace(text, text, public.app_locale) to authenticated;
grant execute on function public.create_workspace_invite(uuid, text) to authenticated;
grant execute on function public.accept_workspace_invite(text) to authenticated;
grant execute on function public.revoke_workspace_invite(uuid) to authenticated;
grant execute on function public.remove_member(uuid, uuid) to authenticated;
