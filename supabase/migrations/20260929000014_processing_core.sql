-- P2 schema part 1: pgvector, the firm's service catalog, processing state on source records,
-- extractions with verified evidence quotes, and LLM budget threshold events.
-- Written by the worker (service role); members read. Excluded clients are hidden on read.

create extension if not exists vector with schema extensions;

-- ---------------------------------------------------------------------------------------------
-- Service catalog (D12): what the firm sells; used to match latent needs (F2) and job posts (F4).
-- ---------------------------------------------------------------------------------------------

create table public.workspace_service (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  name text not null check (char_length(name) between 1 and 120),
  description text not null default '' check (char_length(description) <= 1000),
  keywords text[] not null default '{}' check (cardinality(keywords) <= 20),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, id)
);
create unique index workspace_service_name_idx on public.workspace_service (workspace_id, lower(name));

create trigger workspace_service_set_updated_at before update on public.workspace_service
  for each row execute function private.set_updated_at();

alter table public.workspace_service enable row level security;
create policy workspace_service_select_member on public.workspace_service
  for select to authenticated using (private.is_member(workspace_id));
grant select on public.workspace_service to authenticated;

-- Creates a service (p_service_id null) or replaces an existing one. Owner/admin only.
create function public.save_workspace_service(
  p_workspace_id uuid,
  p_service_id uuid,
  p_name text,
  p_description text default '',
  p_keywords text[] default '{}'
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id uuid;
  v_keywords text[];
begin
  perform private.require_uid();
  if not private.has_role(p_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  select coalesce(array_agg(k order by first_pos), '{}') into v_keywords
    from (select lower(btrim(kw)) as k, min(ord) as first_pos
            from unnest(coalesce(p_keywords, '{}')) with ordinality as u(kw, ord)
           where btrim(kw) <> ''
           group by lower(btrim(kw))) s;

  if p_service_id is null then
    insert into public.workspace_service (workspace_id, name, description, keywords)
    values (p_workspace_id, btrim(p_name), btrim(coalesce(p_description, '')), v_keywords)
    returning id into v_id;
    perform private.write_audit(p_workspace_id, 'service.created', 'workspace_service', v_id);
  else
    update public.workspace_service
       set name = btrim(p_name), description = btrim(coalesce(p_description, '')), keywords = v_keywords
     where workspace_id = p_workspace_id and id = p_service_id
    returning id into v_id;
    if v_id is null then
      raise exception 'service not found' using errcode = 'P0002';
    end if;
    perform private.write_audit(p_workspace_id, 'service.updated', 'workspace_service', v_id);
  end if;
  return v_id;
end;
$$;

create function public.delete_workspace_service(p_service_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_workspace_id uuid;
begin
  perform private.require_uid();
  select workspace_id into v_workspace_id from public.workspace_service where id = p_service_id;
  if v_workspace_id is null
     or not private.has_role(v_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  delete from public.workspace_service where workspace_id = v_workspace_id and id = p_service_id;
  perform private.write_audit(v_workspace_id, 'service.deleted', 'workspace_service', p_service_id);
end;
$$;

grant execute on function public.save_workspace_service(uuid, uuid, text, text, text[]) to authenticated;
grant execute on function public.delete_workspace_service(uuid) to authenticated;

-- ---------------------------------------------------------------------------------------------
-- Processing state on source records (D7).
-- ---------------------------------------------------------------------------------------------

create type public.processing_state as enum ('pending', 'done', 'error', 'skipped_purged', 'not_applicable');

-- New rows default to not_applicable; ingest marks full text records pending (P2 step 5).
alter table public.source_record
  add column processing_state public.processing_state not null default 'not_applicable',
  add column processed_at timestamptz,
  add column processing_version integer not null default 0 check (processing_version >= 0);

update public.source_record
   set processing_state = case
     when policy_at_ingest = 'full' and kind = 'email' and body_clean is not null then 'pending'
     when policy_at_ingest = 'full' and kind = 'email' then 'skipped_purged'
     else 'not_applicable'
   end::public.processing_state;

create index source_record_pending_idx on public.source_record (workspace_id, occurred_at)
  where processing_state = 'pending';

-- Lets derived rows carry the record's client and prove it matches (composite foreign keys below).
alter table public.source_record add constraint source_record_workspace_id_id_client_key
  unique (workspace_id, id, client_id);

-- ---------------------------------------------------------------------------------------------
-- Extractions and evidence (D5, D6). A quote is stored only after it was verified verbatim.
-- ---------------------------------------------------------------------------------------------

create type public.extraction_kind as enum (
  'request', 'commitment', 'praise', 'complaint', 'goal', 'stakeholder_change',
  'in_house_or_competitor_mention', 'renewal_or_budget_talk', 'result_reported', 'tone');

create table public.evidence (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  source_record_id uuid not null,
  client_id uuid not null,
  quote varchar(300) not null
    check (cardinality(regexp_split_to_array(btrim(quote), '\s+')) between 3 and 25),
  -- Character offsets of the quote in the cleaned body: {"start": int, "end": int}.
  locator jsonb not null check (jsonb_typeof(locator) = 'object'),
  deep_link text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, source_record_id, client_id)
    references public.source_record (workspace_id, id, client_id) on delete cascade on update cascade,
  unique (workspace_id, id),
  unique (workspace_id, id, client_id),
  unique (workspace_id, source_record_id, quote)
);
create index evidence_client_idx on public.evidence (workspace_id, client_id);

create table public.extraction (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  source_record_id uuid not null,
  client_id uuid not null,
  kind public.extraction_kind not null,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  model text not null,
  prompt_version text not null,
  processing_version integer not null check (processing_version >= 0),
  -- Position of the item in the model output; makes a retried run idempotent.
  item_index integer not null check (item_index >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, source_record_id, client_id)
    references public.source_record (workspace_id, id, client_id) on delete cascade on update cascade,
  unique (workspace_id, id),
  unique (workspace_id, id, client_id),
  unique (workspace_id, source_record_id, processing_version, item_index)
);
create index extraction_client_kind_idx on public.extraction (workspace_id, client_id, kind);

create table public.extraction_evidence (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  extraction_id uuid not null,
  evidence_id uuid not null,
  client_id uuid not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, extraction_id, client_id)
    references public.extraction (workspace_id, id, client_id) on delete cascade on update cascade,
  foreign key (workspace_id, evidence_id, client_id)
    references public.evidence (workspace_id, id, client_id) on delete cascade on update cascade,
  unique (extraction_id, evidence_id)
);
create index extraction_evidence_evidence_idx on public.extraction_evidence (workspace_id, evidence_id);

-- Evidence or nothing: at commit, every extraction must cite at least one evidence row.
create function private.extraction_requires_evidence()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not exists (
    select 1 from public.extraction_evidence ee
    where ee.workspace_id = new.workspace_id and ee.extraction_id = new.id
  ) then
    raise exception 'extraction requires evidence' using errcode = '23514';
  end if;
  return null;
end;
$$;

create constraint trigger extraction_requires_evidence
  after insert on public.extraction
  deferrable initially deferred
  for each row execute function private.extraction_requires_evidence();

create trigger evidence_set_updated_at before update on public.evidence
  for each row execute function private.set_updated_at();
create trigger extraction_set_updated_at before update on public.extraction
  for each row execute function private.set_updated_at();
create trigger extraction_evidence_set_updated_at before update on public.extraction_evidence
  for each row execute function private.set_updated_at();

alter table public.evidence enable row level security;
alter table public.extraction enable row level security;
alter table public.extraction_evidence enable row level security;

create policy evidence_select_member on public.evidence
  for select to authenticated
  using (private.is_member(workspace_id) and private.client_visible(workspace_id, client_id));
create policy extraction_select_member on public.extraction
  for select to authenticated
  using (private.is_member(workspace_id) and private.client_visible(workspace_id, client_id));
create policy extraction_evidence_select_member on public.extraction_evidence
  for select to authenticated
  using (private.is_member(workspace_id) and private.client_visible(workspace_id, client_id));

grant select on public.evidence, public.extraction, public.extraction_evidence to authenticated;

-- ---------------------------------------------------------------------------------------------
-- LLM budget thresholds (D16): each 50/80/100 % crossing is stored once per workspace and month.
-- ---------------------------------------------------------------------------------------------

create table public.llm_budget_event (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  month date not null check (extract(day from month) = 1),
  threshold smallint not null check (threshold in (50, 80, 100)),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, month, threshold)
);

create trigger llm_budget_event_set_updated_at before update on public.llm_budget_event
  for each row execute function private.set_updated_at();

alter table public.llm_budget_event enable row level security;
create policy llm_budget_event_select_admin on public.llm_budget_event
  for select to authenticated
  using (private.has_role(workspace_id, array['owner', 'admin']::public.member_role[]));
grant select on public.llm_budget_event to authenticated;
