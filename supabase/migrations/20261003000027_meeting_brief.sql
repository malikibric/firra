-- T45: pre-meeting briefs (F1). One brief per (calendar event, start time): a rescheduled meeting
-- gets a new row, a cancelled one is hidden by the read model. Written by the worker (service role);
-- owner/admin read. Quotes live only in `evidence`; `content` references them by id.
--
-- Policy: a client leaving `full` loses its briefs at once (trigger below), so no quote or
-- commitment text outlives the policy that allowed it. `excluded` clients are also hidden on read.

-- The brief points at its event and client together, so it can never cite another client's meeting.
alter table public.calendar_event
  add constraint calendar_event_ws_id_client_key unique (workspace_id, id, client_id);

create table public.meeting_brief (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  calendar_event_id uuid not null,
  client_id uuid not null,
  starts_at timestamptz not null,
  locale text not null default 'en' check (locale ~ '^[a-z]{2}(-[A-Za-z]{2})?$'),
  -- 'evidence' briefs cite verified quotes; 'metadata' briefs (metadata_only clients, or nothing
  -- quotable yet) hold counts, dates and checklist codes only.
  basis public.signal_basis not null,
  content jsonb not null check (jsonb_typeof(content) = 'object'),
  -- 'facts.v1' is the deterministic builder; model and prompt_version stay null until an LLM words it.
  version text not null check (char_length(version) between 1 and 40),
  model text,
  prompt_version text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, calendar_event_id, client_id)
    references public.calendar_event (workspace_id, id, client_id) on delete cascade,
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade,
  unique (workspace_id, id),
  unique (workspace_id, id, client_id),
  unique (workspace_id, calendar_event_id, starts_at)
);
create index meeting_brief_upcoming_idx on public.meeting_brief (workspace_id, starts_at);
create index meeting_brief_client_idx on public.meeting_brief (workspace_id, client_id);

create table public.meeting_brief_evidence (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  meeting_brief_id uuid not null,
  evidence_id uuid not null,
  client_id uuid not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, meeting_brief_id, client_id)
    references public.meeting_brief (workspace_id, id, client_id) on delete cascade on update cascade,
  foreign key (workspace_id, evidence_id, client_id)
    references public.evidence (workspace_id, id, client_id) on delete cascade on update cascade,
  unique (meeting_brief_id, evidence_id)
);
create index meeting_brief_evidence_evidence_idx on public.meeting_brief_evidence (workspace_id, evidence_id);

create trigger meeting_brief_set_updated_at before update on public.meeting_brief
  for each row execute function private.set_updated_at();
create trigger meeting_brief_evidence_set_updated_at before update on public.meeting_brief_evidence
  for each row execute function private.set_updated_at();

-- Evidence or nothing: a brief that quotes must cite at least one evidence row at commit.
create constraint trigger meeting_brief_requires_evidence
  after insert on public.meeting_brief
  deferrable initially deferred
  for each row when (new.basis = 'evidence')
  execute function private.require_evidence('meeting_brief_evidence', 'meeting_brief_id');

alter table public.meeting_brief enable row level security;
alter table public.meeting_brief_evidence enable row level security;
create policy meeting_brief_select_member on public.meeting_brief for select to authenticated
  using (private.is_member(workspace_id) and private.client_visible(workspace_id, client_id));
create policy meeting_brief_evidence_select_member on public.meeting_brief_evidence for select to authenticated
  using (private.is_member(workspace_id) and private.client_visible(workspace_id, client_id));
grant select on public.meeting_brief, public.meeting_brief_evidence to authenticated;

-- A client leaving `full` (metadata_only or excluded) drops its briefs; the next sweep rebuilds a
-- metadata brief for metadata_only clients. Additive on purpose: set_client_policy is unchanged.
create function private.drop_meeting_briefs_on_policy_change()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  delete from public.meeting_brief where workspace_id = new.workspace_id and client_id = new.id;
  return null;
end;
$$;

create trigger client_policy_drops_meeting_briefs
  after update of policy on public.client
  for each row when (old.policy = 'full' and new.policy <> 'full')
  execute function private.drop_meeting_briefs_on_policy_change();
