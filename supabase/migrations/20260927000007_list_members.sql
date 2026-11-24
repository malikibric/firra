-- Members page: emails live in auth.users, which user sessions cannot read.
-- Returns members of one workspace to its own members only.

create function public.list_workspace_members(p_workspace_id uuid)
returns table (user_id uuid, email text, role public.member_role, joined_at timestamptz)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  perform private.require_uid();
  if not private.is_member(p_workspace_id) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  return query
    select m.user_id, u.email::text, m.role, m.created_at
      from public.membership m
      join auth.users u on u.id = m.user_id
     where m.workspace_id = p_workspace_id
     order by m.role, m.created_at;
end;
$$;

grant execute on function public.list_workspace_members(uuid) to authenticated;
