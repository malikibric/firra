-- Milestone E (docs/plans/e-routines.md, T43a): routines surface decision cards by importance.
--
--   E-D1  a surfaced item is a `decision_card`: new kinds, `importance`, `surfaced_on`,
--         `reason_code`, `demoted_reason` and the run that created it. Evidence links, feedback,
--         status and the policy triggers stay exactly as they are.
--   E-D2  `routine`: one row per workspace and first-party routine kind, written only through
--         `set_routine_enabled` (owner/admin, audited) or by the worker (service role).
--   E-D3  `routine_run`: one row per routine and local date, so a retry never doubles an item.
--
-- New enum values cannot be used in the transaction that adds them: nothing below refers to them.

alter type public.decision_kind add value 'state_change';
alter type public.decision_kind add value 'commitment_watch';
alter type public.decision_kind add value 'expansion';

create type public.card_importance as enum ('fyi', 'attention', 'important');
create type public.routine_kind as enum ('morning_scan', 'commitment_watch', 'pre_meeting_brief');
create type public.routine_run_status as enum ('queued', 'running', 'succeeded', 'failed');

-- ---------------------------------------------------------------------------------------------
-- Routine definition and runs.
-- ---------------------------------------------------------------------------------------------

create table public.routine (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  kind public.routine_kind not null,
  enabled boolean not null default true,
  -- Per-workspace overrides of config/routines.yaml (hour, thresholds); empty = defaults.
  params jsonb not null default '{}' check (jsonb_typeof(params) = 'object'),
  -- config/routines.yaml version the worker last ran this routine with; null until the first run.
  definition_version text check (char_length(definition_version) between 1 and 40),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, id),
  unique (workspace_id, kind)
);

create table public.routine_run (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  routine_id uuid not null,
  -- The workspace-local date this run is for (the idempotency key together with the routine).
  scheduled_for date not null,
  status public.routine_run_status not null default 'queued',
  job_id uuid,
  -- Numbers only: {"items": 4, "important": 1, "demoted": 0, "suppressed": 2, ...}. No ids, no text.
  counts jsonb not null default '{}' check (jsonb_typeof(counts) = 'object'),
  error_class text check (char_length(error_class) between 1 and 80),
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, routine_id) references public.routine (workspace_id, id) on delete cascade,
  unique (workspace_id, id),
  unique (routine_id, scheduled_for)
);
create index routine_run_recent_idx on public.routine_run (workspace_id, scheduled_for desc);

create trigger routine_set_updated_at before update on public.routine
  for each row execute function private.set_updated_at();
create trigger routine_run_set_updated_at before update on public.routine_run
  for each row execute function private.set_updated_at();

alter table public.routine enable row level security;
alter table public.routine_run enable row level security;
create policy routine_select_member on public.routine for select to authenticated
  using (private.is_member(workspace_id));
create policy routine_run_select_member on public.routine_run for select to authenticated
  using (private.is_member(workspace_id));
grant select on public.routine, public.routine_run to authenticated;

-- ---------------------------------------------------------------------------------------------
-- Decision cards created by a routine.
-- ---------------------------------------------------------------------------------------------

alter table public.decision_card
  add column importance public.card_importance,
  add column surfaced_on date,
  -- Why the engine raised it (a code from config/routines.yaml); with the client and the kind it is
  -- the suppression key of E-D7.
  add column reason_code text check (reason_code ~ '^[a-z][a-z0-9_]{2,59}$'),
  -- An `important` item that went over the weekly budget is stored as `attention` and says so.
  add column demoted_reason text check (demoted_reason = 'budget'),
  add column routine_run_id uuid,
  add constraint decision_card_routine_run_fkey foreign key (workspace_id, routine_run_id)
    references public.routine_run (workspace_id, id) on delete set null (routine_run_id),
  -- Cards of a routine are fully described; the older weekly cards (no run) stay as they are.
  add constraint decision_card_routine_fields check (
    routine_run_id is null
    or (importance is not null and surfaced_on is not null and reason_code is not null)),
  add constraint decision_card_demoted_is_attention check (
    demoted_reason is null or importance = 'attention');

-- Weekly budget count (E-D6) and suppression lookup (E-D7).
create index decision_card_important_idx on public.decision_card (workspace_id, surfaced_on)
  where importance = 'important';
create index decision_card_suppression_idx
  on public.decision_card (workspace_id, client_id, kind, reason_code, surfaced_on desc)
  where reason_code is not null;

-- ---------------------------------------------------------------------------------------------
-- Owner/admin switch, audited like every user write.
-- ---------------------------------------------------------------------------------------------

create function public.set_routine_enabled(
  p_workspace_id uuid,
  p_kind public.routine_kind,
  p_enabled boolean
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id uuid;
begin
  perform private.require_uid();
  if not private.has_role(p_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  insert into public.routine (workspace_id, kind, enabled)
  values (p_workspace_id, p_kind, p_enabled)
  on conflict (workspace_id, kind) do update set enabled = excluded.enabled
  returning id into v_id;
  perform private.write_audit(p_workspace_id, 'routine.enabled_changed', 'routine', v_id,
    jsonb_build_object('kind', p_kind, 'enabled', p_enabled));
  return v_id;
end;
$$;

revoke execute on function public.set_routine_enabled(uuid, public.routine_kind, boolean) from public;
grant execute on function public.set_routine_enabled(uuid, public.routine_kind, boolean) to authenticated;
