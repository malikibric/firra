-- Owner/admin ends one connection of their workspace (a mailbox of a member, a contributor or an
-- org-wide mode) and wipes its stored token. revoke_contributor ends all of a contributor's
-- connections; this ends exactly one. Needed by the Mac app (docs/plans/d-mac-app.md D-D5).
create function public.disconnect_connection(p_connection_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_workspace_id uuid;
begin
  perform private.require_uid();
  select c.workspace_id into v_workspace_id from public.connection c where c.id = p_connection_id;
  if v_workspace_id is null
     or not private.has_role(v_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  update public.connection set status = 'revoked', token_ciphertext = null where id = p_connection_id;
  perform private.write_audit(v_workspace_id, 'connection.disconnected', 'connection', p_connection_id);
end;
$$;

grant execute on function public.disconnect_connection(uuid) to authenticated;
