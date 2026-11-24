-- LLM usage: tokens and cost per call, for budget caps and cost reporting.

create table public.llm_usage (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  job_id uuid references public.job (id) on delete set null,
  model text not null,
  prompt_version text,
  input_tokens integer not null check (input_tokens >= 0),
  output_tokens integer not null check (output_tokens >= 0),
  cost_usd numeric(12, 6) not null check (cost_usd >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index llm_usage_workspace_created_idx on public.llm_usage (workspace_id, created_at);
create index llm_usage_job_idx on public.llm_usage (job_id);

create trigger llm_usage_set_updated_at before update on public.llm_usage
  for each row execute function private.set_updated_at();

alter table public.llm_usage enable row level security;

create policy llm_usage_select_admin on public.llm_usage
  for select to authenticated
  using (private.has_role(workspace_id, array['owner', 'admin']::public.member_role[]));

grant select on public.llm_usage to authenticated;
