-- P2 schema part 2: derived account memory (commitments, goals, requests, latent needs), signals,
-- the client card and decision cards, plus champion/merge fields on person.
-- Written by the worker (service role); members read. Excluded clients are hidden on read.
--
-- Every derived row carries its client, and every evidence link references
-- (workspace_id, evidence_id, client_id), so a row can never cite another client's evidence.

-- ---------------------------------------------------------------------------------------------
-- People: champion, departure and reversible automatic merges (D8, signals).
-- ---------------------------------------------------------------------------------------------

alter table public.person
  add column is_champion boolean not null default false,
  add column departed_at timestamptz,
  add column merged_into_person_id uuid,
  add constraint person_merged_into_fkey foreign key (workspace_id, merged_into_person_id)
    references public.person (workspace_id, id) on delete set null (merged_into_person_id),
  add constraint person_not_merged_into_self check (merged_into_person_id is distinct from id);

-- ---------------------------------------------------------------------------------------------
-- Enums.
-- ---------------------------------------------------------------------------------------------

create type public.commitment_status as enum ('open', 'done', 'late', 'cancelled');
create type public.latent_need_status as enum ('open', 'addressed', 'dismissed');
create type public.signal_severity as enum ('low', 'medium', 'high');
-- 'evidence' signals cite verified quotes; 'metadata' signals store their numeric inputs instead.
create type public.signal_basis as enum ('evidence', 'metadata');
create type public.decision_kind as enum ('risk', 'margin', 'pricing', 'growth', 'external', 'commitment');
create type public.decision_status as enum ('new', 'done', 'dismissed');

-- ---------------------------------------------------------------------------------------------
-- Commitments and goals (D9).
-- ---------------------------------------------------------------------------------------------

create table public.commitment (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid not null,
  owner_side public.person_side not null,
  text text not null check (char_length(text) between 1 and 500),
  due_date date,
  status public.commitment_status not null default 'open',
  stated_at timestamptz not null,
  dedupe_key text not null check (char_length(dedupe_key) between 1 and 200),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade,
  unique (workspace_id, id),
  unique (workspace_id, id, client_id),
  unique (workspace_id, client_id, dedupe_key)
);
create index commitment_client_status_idx on public.commitment (workspace_id, client_id, status);

create table public.client_goal (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid not null,
  text text not null check (char_length(text) between 1 and 500),
  metric text check (char_length(metric) <= 200),
  stated_at timestamptz not null,
  dedupe_key text not null check (char_length(dedupe_key) between 1 and 200),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade,
  unique (workspace_id, id),
  unique (workspace_id, id, client_id),
  unique (workspace_id, client_id, dedupe_key)
);

-- ---------------------------------------------------------------------------------------------
-- Requests and complaints as embeddable items (D11). Clustering runs per client in Python.
-- ---------------------------------------------------------------------------------------------

create table public.request (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid not null,
  origin_kind public.extraction_kind not null check (origin_kind in ('request', 'complaint')),
  text text not null check (char_length(text) between 1 and 500),
  source_record_id uuid not null,
  thread_key text,
  occurred_at timestamptz not null,
  embedding extensions.vector(768),
  embedding_model text,
  dedupe_key text not null check (char_length(dedupe_key) between 1 and 200),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, source_record_id, client_id)
    references public.source_record (workspace_id, id, client_id) on delete cascade on update cascade,
  unique (workspace_id, id),
  unique (workspace_id, id, client_id),
  unique (workspace_id, client_id, dedupe_key),
  check (embedding is null or embedding_model is not null)
);
create index request_client_occurred_idx on public.request (workspace_id, client_id, occurred_at);
create index request_source_record_idx on public.request (workspace_id, source_record_id);

-- ---------------------------------------------------------------------------------------------
-- Latent needs (F2): counts are computed from latent_need_request, the model only names the topic.
-- ---------------------------------------------------------------------------------------------

create table public.latent_need (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid not null,
  topic text check (char_length(topic) between 1 and 200),
  workspace_service_id uuid,
  mention_count integer not null check (mention_count >= 3),
  thread_count integer not null check (thread_count >= 2),
  first_seen_at timestamptz not null,
  last_seen_at timestamptz not null,
  status public.latent_need_status not null default 'open',
  cluster_key text not null check (char_length(cluster_key) between 1 and 200),
  rule_version text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade,
  foreign key (workspace_id, workspace_service_id)
    references public.workspace_service (workspace_id, id) on delete set null (workspace_service_id),
  unique (workspace_id, id),
  unique (workspace_id, id, client_id),
  unique (workspace_id, client_id, cluster_key),
  check (last_seen_at >= first_seen_at)
);
create index latent_need_client_status_idx on public.latent_need (workspace_id, client_id, status);

create table public.latent_need_request (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  latent_need_id uuid not null,
  request_id uuid not null,
  client_id uuid not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, latent_need_id, client_id)
    references public.latent_need (workspace_id, id, client_id) on delete cascade on update cascade,
  foreign key (workspace_id, request_id, client_id)
    references public.request (workspace_id, id, client_id) on delete cascade on update cascade,
  unique (latent_need_id, request_id)
);
create index latent_need_request_request_idx on public.latent_need_request (workspace_id, request_id);

-- ---------------------------------------------------------------------------------------------
-- Signals (D10). Tone, escalation and delivery complaints always rest on verified quotes.
-- ---------------------------------------------------------------------------------------------

create table public.signal (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid not null,
  type text not null check (type ~ '^[a-z][a-z0-9_]{2,59}$'),
  severity public.signal_severity not null,
  occurred_at timestamptz not null,
  rule_version text not null,
  basis public.signal_basis not null,
  inputs jsonb check (jsonb_typeof(inputs) = 'object'),
  resolved_at timestamptz,
  dedupe_key text not null check (char_length(dedupe_key) between 1 and 200),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade,
  unique (workspace_id, id),
  unique (workspace_id, id, client_id),
  unique (workspace_id, client_id, type, dedupe_key),
  check (basis = 'evidence' or inputs is not null),
  check (type not in ('tone_decline', 'executive_escalation', 'delivery_complaint') or basis = 'evidence')
);
create index signal_client_open_idx on public.signal (workspace_id, client_id, occurred_at)
  where resolved_at is null;

-- ---------------------------------------------------------------------------------------------
-- Client card (D9): one row per version; evidence ids live inside content and are verified by the
-- worker before the version is written.
-- ---------------------------------------------------------------------------------------------

create table public.client_card (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid not null,
  version integer not null check (version >= 1),
  content jsonb not null check (jsonb_typeof(content) = 'object'),
  model text,
  prompt_version text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade,
  unique (workspace_id, id),
  unique (workspace_id, id, client_id),
  unique (workspace_id, client_id, version)
);

-- ---------------------------------------------------------------------------------------------
-- Decision cards (D14).
-- ---------------------------------------------------------------------------------------------

create table public.decision_card (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid not null,
  week date not null check (extract(isodow from week) = 1),
  kind public.decision_kind not null,
  title text not null check (char_length(title) between 1 and 120),
  body text not null check (char_length(body) between 1 and 2000),
  locale text not null check (locale ~ '^[a-z]{2}(-[A-Za-z]{2})?$'),
  score numeric not null,
  priority smallint not null,
  status public.decision_status not null default 'new',
  outcome_note text check (char_length(outcome_note) <= 1000),
  -- Ids of what the card was built from: {"signal_ids": [], "latent_need_ids": [], "commitment_ids": []}.
  inputs jsonb not null check (jsonb_typeof(inputs) = 'object'),
  model text not null,
  prompt_version text not null,
  dedupe_key text not null check (char_length(dedupe_key) between 1 and 200),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade,
  unique (workspace_id, id),
  unique (workspace_id, id, client_id),
  unique (workspace_id, week, dedupe_key)
);
create index decision_card_week_idx on public.decision_card (workspace_id, week, priority);
create index decision_card_client_idx on public.decision_card (workspace_id, client_id, week);

-- ---------------------------------------------------------------------------------------------
-- Evidence links, one table per derived kind, shaped exactly like extraction_evidence.
-- ---------------------------------------------------------------------------------------------

do $do$
declare
  spec record;
begin
  for spec in
    select * from (values
      ('commitment_evidence', 'commitment', 'commitment_id'),
      ('client_goal_evidence', 'client_goal', 'client_goal_id'),
      ('request_evidence', 'request', 'request_id'),
      ('latent_need_evidence', 'latent_need', 'latent_need_id'),
      ('signal_evidence', 'signal', 'signal_id'),
      ('decision_evidence', 'decision_card', 'decision_card_id')
    ) as s (link_table, parent_table, parent_column)
  loop
    execute format($sql$
      create table public.%1$I (
        id uuid primary key default gen_random_uuid(),
        workspace_id uuid not null references public.workspace (id) on delete cascade,
        %3$I uuid not null,
        evidence_id uuid not null,
        client_id uuid not null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        foreign key (workspace_id, %3$I, client_id)
          references public.%2$I (workspace_id, id, client_id) on delete cascade on update cascade,
        foreign key (workspace_id, evidence_id, client_id)
          references public.evidence (workspace_id, id, client_id) on delete cascade on update cascade,
        unique (%3$I, evidence_id)
      )$sql$, spec.link_table, spec.parent_table, spec.parent_column);
    execute format('create index %I on public.%I (workspace_id, evidence_id)',
                   spec.link_table || '_evidence_idx', spec.link_table);
    execute format('create trigger %I before update on public.%I for each row execute function private.set_updated_at()',
                   spec.link_table || '_set_updated_at', spec.link_table);
    execute format('alter table public.%I enable row level security', spec.link_table);
    execute format($sql$
      create policy %I on public.%I for select to authenticated
        using (private.is_member(workspace_id) and private.client_visible(workspace_id, client_id))$sql$,
      spec.link_table || '_select_member', spec.link_table);
    execute format('grant select on public.%I to authenticated', spec.link_table);
  end loop;
end
$do$;

-- ---------------------------------------------------------------------------------------------
-- Evidence or nothing: at commit, a derived row must cite at least one evidence row.
-- ---------------------------------------------------------------------------------------------

create function private.require_evidence()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_found boolean;
begin
  -- tg_argv comes from the trigger definitions below: the link table and its parent column.
  execute format('select exists (select 1 from public.%I where workspace_id = $1 and %I = $2)',
                 tg_argv[0], tg_argv[1])
    into v_found using new.workspace_id, new.id;
  if not v_found then
    raise exception '% requires evidence', tg_table_name using errcode = '23514';
  end if;
  return null;
end;
$$;

create constraint trigger commitment_requires_evidence
  after insert on public.commitment
  deferrable initially deferred
  for each row execute function private.require_evidence('commitment_evidence', 'commitment_id');
create constraint trigger client_goal_requires_evidence
  after insert on public.client_goal
  deferrable initially deferred
  for each row execute function private.require_evidence('client_goal_evidence', 'client_goal_id');
create constraint trigger request_requires_evidence
  after insert on public.request
  deferrable initially deferred
  for each row execute function private.require_evidence('request_evidence', 'request_id');
create constraint trigger latent_need_requires_evidence
  after insert on public.latent_need
  deferrable initially deferred
  for each row execute function private.require_evidence('latent_need_evidence', 'latent_need_id');
create constraint trigger signal_requires_evidence
  after insert on public.signal
  deferrable initially deferred
  for each row when (new.basis = 'evidence')
  execute function private.require_evidence('signal_evidence', 'signal_id');
create constraint trigger decision_card_requires_evidence
  after insert on public.decision_card
  deferrable initially deferred
  for each row execute function private.require_evidence('decision_evidence', 'decision_card_id');

-- ---------------------------------------------------------------------------------------------
-- updated_at triggers and read-only RLS for the parent tables.
-- ---------------------------------------------------------------------------------------------

do $do$
declare
  t text;
begin
  foreach t in array array['commitment', 'client_goal', 'request', 'latent_need', 'latent_need_request',
                           'signal', 'client_card', 'decision_card']
  loop
    execute format('create trigger %I before update on public.%I for each row execute function private.set_updated_at()',
                   t || '_set_updated_at', t);
    execute format('alter table public.%I enable row level security', t);
    execute format($sql$
      create policy %I on public.%I for select to authenticated
        using (private.is_member(workspace_id) and private.client_visible(workspace_id, client_id))$sql$,
      t || '_select_member', t);
    execute format('grant select on public.%I to authenticated', t);
  end loop;
end
$do$;
