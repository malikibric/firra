-- People seen in client communication (client side) and the firm's own team (firm side).
-- Written by the worker (entity resolution); members read them.

create type public.person_side as enum ('client', 'firm');
create type public.identity_kind as enum ('email', 'slack_id', 'pm_id', 'crm_id');

create table public.person (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid,
  side public.person_side not null,
  display_name text check (char_length(display_name) <= 200),
  role_title text check (char_length(role_title) <= 200),
  is_decision_maker boolean not null default false,
  first_seen_at timestamptz,
  last_seen_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete set null (client_id),
  unique (workspace_id, id)
);
create index person_client_idx on public.person (workspace_id, client_id);

create table public.person_identity (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  person_id uuid not null,
  kind public.identity_kind not null,
  value text not null check (char_length(value) between 1 and 320),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, person_id) references public.person (workspace_id, id) on delete cascade,
  unique (workspace_id, kind, value),
  -- Emails are stored normalized so lookups are exact.
  check (kind <> 'email' or value = lower(value))
);
create index person_identity_person_idx on public.person_identity (workspace_id, person_id);

create trigger person_set_updated_at before update on public.person
  for each row execute function private.set_updated_at();
create trigger person_identity_set_updated_at before update on public.person_identity
  for each row execute function private.set_updated_at();

alter table public.person enable row level security;
alter table public.person_identity enable row level security;

create policy person_select_member on public.person
  for select to authenticated using (private.is_member(workspace_id));
create policy person_identity_select_member on public.person_identity
  for select to authenticated using (private.is_member(workspace_id));

grant select on public.person, public.person_identity to authenticated;
