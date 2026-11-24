-- Normalized source records (emails, calendar events, …) kept after minimization, plus the
-- client data-policy RPC that enforces policy changes on already stored data.

create type public.record_kind as enum ('email', 'chat', 'call', 'transcript', 'task', 'comment',
  'time_entry', 'invoice', 'contract', 'calendar_event', 'crm_note');
create type public.record_direction as enum ('in', 'out', 'internal');
create type public.event_status as enum ('scheduled', 'held', 'cancelled', 'rescheduled');

create table public.source_record (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  connection_id uuid not null,
  provider public.connection_provider not null,
  external_id text not null check (char_length(external_id) between 1 and 512),
  kind public.record_kind not null,
  client_id uuid,
  policy_at_ingest public.data_policy not null,
  thread_key text check (char_length(thread_key) <= 512),
  occurred_at timestamptz not null,
  author_person_id uuid,
  direction public.record_direction not null,
  participants uuid[] not null default '{}',
  word_count integer not null default 0 check (word_count >= 0),
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  -- Cleaned text body; null for metadata_only and after the 15-day purge.
  body_clean text,
  body_purged_at timestamptz,
  raw_object_uri text,
  deep_link text,
  content_hash bytea,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, connection_id) references public.connection (workspace_id, id) on delete cascade,
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade,
  foreign key (workspace_id, author_person_id) references public.person (workspace_id, id)
    on delete set null (author_person_id),
  unique (connection_id, external_id),
  unique (workspace_id, id),
  check (policy_at_ingest <> 'excluded'),
  check (policy_at_ingest = 'full' or (body_clean is null and raw_object_uri is null))
);
create index source_record_client_time_idx on public.source_record (workspace_id, client_id, occurred_at desc);
create index source_record_purge_idx on public.source_record (created_at) where body_clean is not null;

create table public.calendar_event (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  source_record_id uuid not null unique,
  client_id uuid,
  starts_at timestamptz not null,
  duration_min integer not null check (duration_min >= 0),
  status public.event_status not null,
  attendee_count integer not null check (attendee_count >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, source_record_id) references public.source_record (workspace_id, id) on delete cascade,
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade
);
create index calendar_event_client_idx on public.calendar_event (workspace_id, client_id, starts_at desc);

create trigger source_record_set_updated_at before update on public.source_record
  for each row execute function private.set_updated_at();
create trigger calendar_event_set_updated_at before update on public.calendar_event
  for each row execute function private.set_updated_at();

-- Read-path enforcement: records of excluded clients are invisible even if a row survived.
create function private.client_visible(p_workspace_id uuid, p_client_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select p_client_id is null or exists (
    select 1 from public.client c
    where c.workspace_id = p_workspace_id and c.id = p_client_id and c.policy <> 'excluded'
  );
$$;
grant execute on function private.client_visible(uuid, uuid) to authenticated, service_role;

alter table public.source_record enable row level security;
alter table public.calendar_event enable row level security;

create policy source_record_select_member on public.source_record
  for select to authenticated
  using (private.is_member(workspace_id) and private.client_visible(workspace_id, client_id));
create policy calendar_event_select_member on public.calendar_event
  for select to authenticated
  using (private.is_member(workspace_id) and private.client_visible(workspace_id, client_id));

grant select on public.source_record, public.calendar_event to authenticated;

-- Changes a client's data policy and applies it to data already stored:
--   excluded      -> delete the client's records
--   metadata_only -> drop bodies and raw object references (objects expire via GCS lifecycle)
create function public.set_client_policy(p_client_id uuid, p_policy public.data_policy)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_workspace_id uuid;
  v_previous public.data_policy;
  v_affected integer := 0;
begin
  perform private.require_uid();
  select workspace_id, policy into v_workspace_id, v_previous from public.client where id = p_client_id;
  if v_workspace_id is null
     or not private.has_role(v_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  update public.client set policy = p_policy where id = p_client_id;

  if p_policy = 'excluded' then
    delete from public.source_record where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_affected = row_count;
  elsif p_policy = 'metadata_only' then
    update public.source_record
       set body_clean = null, raw_object_uri = null, policy_at_ingest = 'metadata_only',
           body_purged_at = coalesce(body_purged_at, now())
     where workspace_id = v_workspace_id and client_id = p_client_id and policy_at_ingest = 'full';
    get diagnostics v_affected = row_count;
  end if;

  perform private.write_audit(v_workspace_id, 'client.policy_changed', 'client', p_client_id,
    jsonb_build_object('from', v_previous, 'to', p_policy, 'records_affected', v_affected));
end;
$$;

grant execute on function public.set_client_policy(uuid, public.data_policy) to authenticated;
