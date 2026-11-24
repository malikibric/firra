-- Audit trail: append-only record of security-relevant actions and client-data reads.
-- Holds ids and short action names only; never message bodies, quotes, or tokens.

create table public.audit_log (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  actor_type public.actor_type not null,
  actor_id uuid,
  action text not null check (action ~ '^[a-z][a-z_]*(\.[a-z][a-z_]*)+$'),
  target_type text check (target_type ~ '^[a-z][a-z_]*$'),
  target_id uuid,
  ip inet,
  user_agent text check (char_length(user_agent) <= 512),
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index audit_log_workspace_created_idx on public.audit_log (workspace_id, created_at desc);

create function private.reject_audit_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'audit_log is append-only' using errcode = 'P0001';
end;
$$;

create trigger audit_log_append_only before update on public.audit_log
  for each row execute function private.reject_audit_update();

alter table public.audit_log enable row level security;

create policy audit_log_select_admin on public.audit_log
  for select to authenticated
  using (private.has_role(workspace_id, array['owner', 'admin']::public.member_role[]));

grant select on public.audit_log to authenticated;

-- Informational request context passed by our own servers via PostgREST headers.
-- Not trusted for authorization decisions.
create function private.request_header(p_name text)
returns text
language sql
stable
set search_path = ''
as $$
  select nullif(current_setting('request.headers', true), '')::json ->> p_name;
$$;

create function private.write_audit(
  p_workspace_id uuid,
  p_action text,
  p_target_type text default null,
  p_target_id uuid default null,
  p_metadata jsonb default '{}'::jsonb
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ip inet;
begin
  begin
    v_ip := nullif(btrim(split_part(private.request_header('x-client-ip'), ',', 1)), '')::inet;
  exception when invalid_text_representation then
    v_ip := null;
  end;
  insert into public.audit_log (workspace_id, actor_type, actor_id, action, target_type, target_id,
                                ip, user_agent, metadata)
  values (
    p_workspace_id,
    case when auth.uid() is null then 'system' else 'user' end::public.actor_type,
    auth.uid(),
    p_action,
    p_target_type,
    p_target_id,
    v_ip,
    left(private.request_header('x-client-user-agent'), 512),
    p_metadata
  );
end;
$$;
