-- Jobs: one row per unit of async work; tasks are idempotent on (workspace_id, idempotency_key).
-- Written by apps/api and services/worker (service role) only; members may read progress.

create table public.job (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  type text not null check (type ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$'),
  status public.job_status not null default 'queued',
  idempotency_key text not null check (char_length(idempotency_key) between 1 and 200),
  attempts integer not null default 0 check (attempts >= 0),
  max_attempts integer not null default 5 check (max_attempts > 0),
  -- Task payload (ids and parameters only, never content), kept for re-enqueue and debugging.
  payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  progress jsonb not null default '{}'::jsonb check (jsonb_typeof(progress) = 'object'),
  error_class text check (error_class ~ '^[a-z][a-z0-9_]*$'),
  -- FK to connection is added in P1 together with the connection table.
  connection_id uuid,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, idempotency_key)
);
create index job_workspace_status_idx on public.job (workspace_id, status);
create index job_workspace_created_idx on public.job (workspace_id, created_at desc);

create trigger job_set_updated_at before update on public.job
  for each row execute function private.set_updated_at();

alter table public.job enable row level security;

create policy job_select_member on public.job
  for select to authenticated using (private.is_member(workspace_id));

grant select on public.job to authenticated;
