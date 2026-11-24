-- MCP personal access tokens (docs/plans/f-mcp.md, F-D4).
-- The secret (`frm_` + 64 hex characters) is generated inside the database, shown to the caller once
-- and stored only as its sha256. A token belongs to one workspace member and dies with the membership.

create table public.mcp_token (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  user_id uuid not null,
  name text not null check (char_length(btrim(name)) between 1 and 80),
  token_hash bytea not null unique check (octet_length(token_hash) = 32),
  -- First characters of the secret, shown in lists so a token can be recognised and revoked.
  token_prefix text not null check (token_prefix ~ '^frm_[0-9a-f]{8}$'),
  scopes text[] not null default array['read'] check (cardinality(scopes) >= 1 and scopes <@ array['read']),
  expires_at timestamptz not null,
  last_used_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, user_id)
    references public.membership (workspace_id, user_id) on delete cascade,
  unique (workspace_id, id),
  check (expires_at > created_at)
);
create index mcp_token_workspace_idx on public.mcp_token (workspace_id, created_at desc);

create trigger mcp_token_set_updated_at before update on public.mcp_token
  for each row execute function private.set_updated_at();

alter table public.mcp_token enable row level security;

create policy mcp_token_select_admin on public.mcp_token
  for select to authenticated
  using (private.has_role(workspace_id, array['owner', 'admin']::public.member_role[]));

-- The hash never leaves the database for users.
grant select (id, workspace_id, user_id, name, token_prefix, scopes, expires_at, last_used_at,
              revoked_at, created_at, updated_at)
  on public.mcp_token to authenticated;

create function public.create_mcp_token(p_workspace_id uuid, p_name text, p_days integer default 90)
returns table (token_id uuid, token text, token_prefix text, expires_at timestamptz)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := private.require_uid();
  v_token text := 'frm_' || encode(extensions.gen_random_bytes(32), 'hex');
  v_row public.mcp_token;
begin
  if not private.has_role(p_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  if p_days is null or p_days < 1 or p_days > 365 then
    raise exception 'invalid lifetime' using errcode = '22023';
  end if;
  if (select count(*) from public.mcp_token t
       where t.workspace_id = p_workspace_id and t.user_id = v_uid
         and t.revoked_at is null and t.expires_at > now()) >= 10 then
    raise exception 'too many active tokens' using errcode = '54000';
  end if;

  insert into public.mcp_token (workspace_id, user_id, name, token_hash, token_prefix, expires_at)
  values (p_workspace_id, v_uid, btrim(p_name), sha256(convert_to(v_token, 'UTF8')),
          substr(v_token, 1, 12), now() + make_interval(days => p_days))
  returning * into v_row;

  perform private.write_audit(p_workspace_id, 'mcp_token.created', 'mcp_token', v_row.id,
    jsonb_build_object('days', p_days));

  token_id := v_row.id;
  token := v_token;
  token_prefix := v_row.token_prefix;
  expires_at := v_row.expires_at;
  return next;
end;
$$;

create function public.revoke_mcp_token(p_token_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_workspace_id uuid;
begin
  select workspace_id into v_workspace_id from public.mcp_token where id = p_token_id;
  if v_workspace_id is null
     or not private.has_role(v_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  update public.mcp_token set revoked_at = now() where id = p_token_id and revoked_at is null;
  perform private.write_audit(v_workspace_id, 'mcp_token.revoked', 'mcp_token', p_token_id);
end;
$$;

grant execute on function public.create_mcp_token(uuid, text, integer) to authenticated;
grant execute on function public.revoke_mcp_token(uuid) to authenticated;
