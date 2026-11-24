-- P2b operator core (the design notes): signal history, account assessments and replay
-- runs, dated person observations, commitment closure times, structured decision cards, founder
-- feedback and client outcomes. Written by the worker (service role) except feedback, outcomes and
-- replay requests, which go through audited owner/admin RPCs. Members read; excluded clients are
-- hidden on read and their rows deleted by set_client_policy.

-- ---------------------------------------------------------------------------------------------
-- Enums.
-- ---------------------------------------------------------------------------------------------

create type public.signal_change as enum ('opened', 'severity_changed', 'reopened', 'resolved');
create type public.risk_state as enum ('stable', 'watch', 'elevated', 'high');
create type public.expansion_state as enum ('none', 'hypothesis', 'strong');
create type public.evidence_strength as enum ('weak', 'moderate', 'strong');
create type public.assessment_run_kind as enum ('live', 'replay');
create type public.replay_status as enum ('queued', 'running', 'succeeded', 'failed');
create type public.feedback_verdict as enum ('useful', 'already_known', 'wrong', 'not_now');
create type public.feedback_action as enum ('taken', 'planned', 'none');
create type public.outcome_event as enum ('renewed', 'price_increase', 'downgrade', 'expanded', 'churned', 'referral');
create type public.outcome_source as enum ('user', 'crm', 'contract');
create type public.observation_source as enum ('signature', 'stakeholder_change', 'manual');

-- Extraction v2 (D10). Not used in this migration: a new enum value is usable after commit.
alter type public.extraction_kind add value 'initiative';

-- ---------------------------------------------------------------------------------------------
-- Signal history (D1): signal keeps the current state, signal_event every change of a live run.
-- ---------------------------------------------------------------------------------------------

alter table public.signal
  add column family text check (family ~ '^[a-z][a-z_]{2,29}$');

create table public.signal_event (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid not null,
  signal_id uuid not null,
  change public.signal_change not null,
  severity public.signal_severity not null,
  as_of timestamptz not null,
  rule_version text not null,
  inputs jsonb check (jsonb_typeof(inputs) = 'object'),
  job_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, signal_id, client_id)
    references public.signal (workspace_id, id, client_id) on delete cascade on update cascade
);
create index signal_event_client_idx on public.signal_event (workspace_id, client_id, as_of);
create index signal_event_signal_idx on public.signal_event (workspace_id, signal_id);

create function private.reject_signal_event_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'signal_event is append-only' using errcode = 'P0001';
end;
$$;

create trigger signal_event_append_only before update on public.signal_event
  for each row execute function private.reject_signal_event_update();

-- ---------------------------------------------------------------------------------------------
-- Replay runs (D8) and account assessments (D6).
-- ---------------------------------------------------------------------------------------------

create table public.replay_run (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  status public.replay_status not null default 'queued',
  window_start date not null,
  window_end date not null,
  step_days integer not null default 7 check (step_days between 1 and 31),
  assessment_version text,
  rule_version text,
  requested_by uuid,
  job_id uuid,
  -- Client ids, dates and counts only; never text from records.
  summary jsonb check (jsonb_typeof(summary) = 'object'),
  error_class text check (char_length(error_class) <= 100),
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, id),
  check (window_end >= window_start and window_end - window_start <= 400)
);
create index replay_run_workspace_idx on public.replay_run (workspace_id, created_at desc);

create table public.account_assessment (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid not null,
  as_of date not null,
  run_kind public.assessment_run_kind not null,
  replay_run_id uuid,
  assessment_version text not null,
  rule_version text not null,
  risk_state public.risk_state not null,
  previous_risk_state public.risk_state,
  state_since date not null,
  expansion_state public.expansion_state not null,
  strength public.evidence_strength not null,
  -- [{type, family, severity, basis, signal_id, occurred_at, inputs, evidence_ids, primary}]
  drivers jsonb not null default '[]' check (jsonb_typeof(drivers) = 'array'),
  -- [{code, detail, ...ids or numbers}]
  caveats jsonb not null default '[]' check (jsonb_typeof(caveats) = 'array'),
  data jsonb not null default '{}' check (jsonb_typeof(data) = 'object'),
  play text check (play ~ '^[a-z][a-z_]{2,39}$'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade,
  foreign key (workspace_id, replay_run_id) references public.replay_run (workspace_id, id) on delete cascade,
  unique (workspace_id, id),
  unique (workspace_id, id, client_id),
  unique nulls not distinct (workspace_id, client_id, run_kind, replay_run_id, as_of),
  check ((run_kind = 'replay') = (replay_run_id is not null)),
  check (state_since <= as_of)
);
create index account_assessment_live_idx on public.account_assessment (workspace_id, client_id, as_of desc)
  where run_kind = 'live';
create index account_assessment_replay_idx on public.account_assessment (workspace_id, replay_run_id, client_id, as_of)
  where run_kind = 'replay';

create table public.assessment_evidence (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  assessment_id uuid not null,
  evidence_id uuid not null,
  client_id uuid not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, assessment_id, client_id)
    references public.account_assessment (workspace_id, id, client_id) on delete cascade on update cascade,
  foreign key (workspace_id, evidence_id, client_id)
    references public.evidence (workspace_id, id, client_id) on delete cascade on update cascade,
  unique (assessment_id, evidence_id)
);
create index assessment_evidence_evidence_idx on public.assessment_evidence (workspace_id, evidence_id);

create table public.assessment_signal (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  assessment_id uuid not null,
  signal_id uuid not null,
  client_id uuid not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, assessment_id, client_id)
    references public.account_assessment (workspace_id, id, client_id) on delete cascade on update cascade,
  foreign key (workspace_id, signal_id, client_id)
    references public.signal (workspace_id, id, client_id) on delete cascade on update cascade,
  unique (assessment_id, signal_id)
);
create index assessment_signal_signal_idx on public.assessment_signal (workspace_id, signal_id);

-- ---------------------------------------------------------------------------------------------
-- Dated person observations (D5) and commitment closure times.
-- ---------------------------------------------------------------------------------------------

alter table public.person add constraint person_workspace_id_client_id_key unique (workspace_id, id, client_id);

create table public.person_observation (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid not null,
  person_id uuid not null,
  observed_at timestamptz not null,
  source public.observation_source not null,
  role_title text check (char_length(role_title) between 1 and 120),
  is_decision_maker boolean not null default false,
  source_record_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade,
  foreign key (workspace_id, person_id, client_id)
    references public.person (workspace_id, id, client_id) on delete cascade,
  foreign key (workspace_id, source_record_id, client_id)
    references public.source_record (workspace_id, id, client_id) on delete set null (source_record_id),
  unique (workspace_id, person_id, source, observed_at)
);
create index person_observation_client_idx on public.person_observation (workspace_id, client_id, observed_at);

alter table public.commitment
  add column closed_at timestamptz,
  add constraint commitment_closed_at_matches_status
    check ((status in ('done', 'cancelled')) = (closed_at is not null));

-- ---------------------------------------------------------------------------------------------
-- Structured decision cards (D9). The table is still empty, so the new columns are required.
-- ---------------------------------------------------------------------------------------------

alter table public.decision_card
  add column assessment_id uuid,
  add column play text not null check (play ~ '^[a-z][a-z_]{2,39}$'),
  -- [{id, text, basis: evidence|metadata, evidence_ids | inputs}]
  add column facts jsonb not null check (jsonb_typeof(facts) = 'array'),
  add column interpretation text not null check (char_length(interpretation) between 1 and 600),
  add column recommendation text not null check (char_length(recommendation) between 1 and 600),
  add column strength public.evidence_strength not null,
  add column caveats jsonb not null default '[]' check (jsonb_typeof(caveats) = 'array'),
  add constraint decision_card_assessment_fkey foreign key (workspace_id, assessment_id, client_id)
    references public.account_assessment (workspace_id, id, client_id) on delete set null (assessment_id);

-- Every fact names its basis; a card needs an evidence link unless every fact is a metadata fact
-- (counts, dates). Checked at commit on insert and whenever the facts change.
create function private.card_requires_evidence()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if exists (select 1 from jsonb_array_elements(new.facts) f
              where jsonb_typeof(f) <> 'object' or coalesce(f ->> 'basis', '') not in ('evidence', 'metadata')) then
    raise exception 'decision_card facts need a basis of evidence or metadata' using errcode = '23514';
  end if;
  if exists (select 1 from public.decision_evidence
              where workspace_id = new.workspace_id and decision_card_id = new.id)
     or not exists (select 1 from jsonb_array_elements(new.facts) f where f ->> 'basis' = 'evidence') then
    return null;
  end if;
  raise exception 'decision_card requires evidence' using errcode = '23514';
end;
$$;

drop trigger decision_card_requires_evidence on public.decision_card;
create constraint trigger decision_card_requires_evidence
  after insert or update of facts on public.decision_card
  deferrable initially deferred
  for each row execute function private.card_requires_evidence();

-- ---------------------------------------------------------------------------------------------
-- Founder feedback and client outcomes (D11).
-- ---------------------------------------------------------------------------------------------

create table public.decision_feedback (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid not null,
  decision_card_id uuid not null,
  user_id uuid not null,
  verdict public.feedback_verdict not null,
  action public.feedback_action,
  note text check (char_length(note) <= 1000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, decision_card_id, client_id)
    references public.decision_card (workspace_id, id, client_id) on delete cascade on update cascade,
  unique (decision_card_id, user_id)
);
create index decision_feedback_client_idx on public.decision_feedback (workspace_id, client_id);

create table public.outcome (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  client_id uuid not null,
  event public.outcome_event not null,
  occurred_at date not null,
  notice_at date,
  first_known_at date,
  amount_change numeric(12, 2),
  currency char(3) check (currency ~ '^[A-Z]{3}$'),
  source public.outcome_source not null default 'user',
  note text check (char_length(note) <= 1000),
  recorded_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete cascade,
  unique (workspace_id, id),
  check (notice_at is null or notice_at <= occurred_at),
  check (first_known_at is null or first_known_at <= occurred_at),
  check ((amount_change is null) = (currency is null))
);
create index outcome_client_idx on public.outcome (workspace_id, client_id, occurred_at);

-- ---------------------------------------------------------------------------------------------
-- updated_at triggers and read-only RLS.
-- ---------------------------------------------------------------------------------------------

do $do$
declare
  t text;
begin
  foreach t in array array['signal_event', 'replay_run', 'account_assessment', 'assessment_evidence',
                           'assessment_signal', 'person_observation', 'decision_feedback', 'outcome']
  loop
    execute format('create trigger %I before update on public.%I for each row execute function private.set_updated_at()',
                   t || '_set_updated_at', t);
    execute format('alter table public.%I enable row level security', t);
    if t = 'replay_run' then
      execute format($sql$
        create policy %I on public.%I for select to authenticated using (private.is_member(workspace_id))$sql$,
        t || '_select_member', t);
    else
      execute format($sql$
        create policy %I on public.%I for select to authenticated
          using (private.is_member(workspace_id) and private.client_visible(workspace_id, client_id))$sql$,
        t || '_select_member', t);
    end if;
    execute format('grant select on public.%I to authenticated', t);
  end loop;
end
$do$;

-- ---------------------------------------------------------------------------------------------
-- RPCs: the only user write paths for feedback, outcomes and replay requests.
-- ---------------------------------------------------------------------------------------------

-- Shared by the RPC and, from P3, signed feedback links handled by apps/api.
create function private.apply_decision_feedback(
  p_workspace_id uuid,
  p_decision_card_id uuid,
  p_user_id uuid,
  p_verdict public.feedback_verdict,
  p_action public.feedback_action,
  p_note text
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_client_id uuid;
  v_id uuid;
begin
  select client_id into v_client_id from public.decision_card
   where workspace_id = p_workspace_id and id = p_decision_card_id;
  if v_client_id is null then
    raise exception 'decision card not found' using errcode = 'P0002';
  end if;

  insert into public.decision_feedback (workspace_id, client_id, decision_card_id, user_id, verdict, action, note)
  values (p_workspace_id, v_client_id, p_decision_card_id, p_user_id, p_verdict, p_action, nullif(btrim(p_note), ''))
  on conflict (decision_card_id, user_id) do update
     set verdict = excluded.verdict, action = excluded.action, note = excluded.note
  returning id into v_id;

  update public.decision_card
     set status = case
                    when p_action = 'taken' then 'done'
                    when p_verdict in ('wrong', 'not_now') then 'dismissed'
                    else status
                  end::public.decision_status
   where workspace_id = p_workspace_id and id = p_decision_card_id;
  return v_id;
end;
$$;

create function public.record_decision_feedback(
  p_decision_card_id uuid,
  p_verdict public.feedback_verdict,
  p_action public.feedback_action default null,
  p_note text default null
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := private.require_uid();
  v_workspace_id uuid;
  v_client_id uuid;
  v_id uuid;
begin
  select workspace_id, client_id into v_workspace_id, v_client_id
    from public.decision_card where id = p_decision_card_id;
  if v_workspace_id is null
     or not private.has_role(v_workspace_id, array['owner', 'admin']::public.member_role[])
     or not private.client_visible(v_workspace_id, v_client_id) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  v_id := private.apply_decision_feedback(v_workspace_id, p_decision_card_id, v_uid, p_verdict, p_action, p_note);
  perform private.write_audit(v_workspace_id, 'decision_feedback.recorded', 'decision_card', p_decision_card_id,
    jsonb_build_object('verdict', p_verdict, 'action', p_action));
  return v_id;
end;
$$;

create function public.record_client_outcome(
  p_client_id uuid,
  p_event public.outcome_event,
  p_occurred_at date,
  p_notice_at date default null,
  p_first_known_at date default null,
  p_amount_change numeric default null,
  p_currency text default null,
  p_note text default null
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := private.require_uid();
  v_workspace_id uuid;
  v_id uuid;
begin
  select workspace_id into v_workspace_id from public.client where id = p_client_id;
  if v_workspace_id is null
     or not private.has_role(v_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  insert into public.outcome (workspace_id, client_id, event, occurred_at, notice_at, first_known_at,
                              amount_change, currency, source, note, recorded_by)
  values (v_workspace_id, p_client_id, p_event, p_occurred_at, p_notice_at, p_first_known_at,
          p_amount_change, upper(nullif(btrim(p_currency), '')), 'user', nullif(btrim(p_note), ''), v_uid)
  returning id into v_id;

  if p_event = 'churned' then
    update public.client set status = 'churned', churned_at = p_occurred_at
     where workspace_id = v_workspace_id and id = p_client_id;
  end if;

  perform private.write_audit(v_workspace_id, 'outcome.recorded', 'outcome', v_id,
    jsonb_build_object('client_id', p_client_id, 'event', p_event));
  return v_id;
end;
$$;

create function public.delete_client_outcome(p_outcome_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_workspace_id uuid;
  v_client_id uuid;
  v_event public.outcome_event;
  v_occurred date;
  v_latest_churn date;
begin
  perform private.require_uid();
  select workspace_id, client_id, event, occurred_at into v_workspace_id, v_client_id, v_event, v_occurred
    from public.outcome where id = p_outcome_id;
  if v_workspace_id is null
     or not private.has_role(v_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  delete from public.outcome where workspace_id = v_workspace_id and id = p_outcome_id;

  -- A deleted churn outcome takes the churn it recorded with it: the latest remaining churn outcome
  -- decides, and with none left a churn this outcome set is undone (a churn set by hand stays).
  if v_event = 'churned' then
    select max(occurred_at) into v_latest_churn from public.outcome
     where workspace_id = v_workspace_id and client_id = v_client_id and event = 'churned';
    if v_latest_churn is not null then
      update public.client set churned_at = v_latest_churn
       where workspace_id = v_workspace_id and id = v_client_id and status = 'churned';
    else
      update public.client set status = 'active', churned_at = null
       where workspace_id = v_workspace_id and id = v_client_id and status = 'churned'
         and churned_at = v_occurred;
    end if;
  end if;

  perform private.write_audit(v_workspace_id, 'outcome.deleted', 'outcome', p_outcome_id,
    jsonb_build_object('client_id', v_client_id, 'event', v_event));
end;
$$;

create function public.request_replay_run(
  p_workspace_id uuid,
  p_window_start date,
  p_window_end date,
  p_step_days integer default 7
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := private.require_uid();
  v_id uuid;
begin
  if not private.has_role(p_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  insert into public.replay_run (workspace_id, window_start, window_end, step_days, requested_by)
  values (p_workspace_id, p_window_start, p_window_end, p_step_days, v_uid)
  returning id into v_id;
  perform private.write_audit(p_workspace_id, 'replay_run.requested', 'replay_run', v_id,
    jsonb_build_object('window_start', p_window_start, 'window_end', p_window_end));
  return v_id;
end;
$$;

grant execute on function public.record_decision_feedback(uuid, public.feedback_verdict, public.feedback_action, text)
  to authenticated;
grant execute on function public.record_client_outcome(uuid, public.outcome_event, date, date, date, numeric, text, text)
  to authenticated;
grant execute on function public.delete_client_outcome(uuid) to authenticated;
grant execute on function public.request_replay_run(uuid, date, date, integer) to authenticated;

-- ---------------------------------------------------------------------------------------------
-- set_client_policy reaches the P2b tables (on top of 20260929000016).
--   excluded      -> assessments (every run), observations and the client's replay summary entries go.
--   metadata_only -> assessments go (recomputed from metadata on the next run), observations learned
--                    from bodies go, and the people's title/decision-maker cache is rebuilt from the
--                    observations that remain (manual ones).
-- Signal events, assessment links and feedback follow their parents by cascade; outcomes stay (the
-- firm's own business facts, hidden by RLS while the client is excluded).
-- ---------------------------------------------------------------------------------------------

create or replace function public.set_client_policy(p_client_id uuid, p_policy public.data_policy)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_workspace_id uuid;
  v_previous public.data_policy;
  v_affected integer := 0;
  v_n integer;
  v_people uuid[];
  v_deleted jsonb := '{}'::jsonb;
begin
  perform private.require_uid();
  select workspace_id, policy into v_workspace_id, v_previous from public.client where id = p_client_id;
  if v_workspace_id is null
     or not private.has_role(v_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  -- Takes the client row lock the evidence trigger's share lock waits on.
  update public.client set policy = p_policy where id = p_client_id;

  if p_policy = 'excluded' then
    -- Derived rows that hang off records go with the records; count them first.
    select count(*) into v_n from public.evidence where workspace_id = v_workspace_id and client_id = p_client_id;
    v_deleted := v_deleted || jsonb_build_object('evidence', v_n);
    select count(*) into v_n from public.extraction where workspace_id = v_workspace_id and client_id = p_client_id;
    v_deleted := v_deleted || jsonb_build_object('extraction', v_n);
    select count(*) into v_n from public.request where workspace_id = v_workspace_id and client_id = p_client_id;
    v_deleted := v_deleted || jsonb_build_object('request', v_n);

    delete from public.source_record where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_affected = row_count;

    -- Derived rows that hang off the client only.
    delete from public.commitment where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('commitment', v_n);
    delete from public.client_goal where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('client_goal', v_n);
    delete from public.latent_need where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('latent_need', v_n);
    delete from public.signal where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('signal', v_n);
    delete from public.decision_card where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('decision_card', v_n);
    delete from public.client_card where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('client_card', v_n);
    delete from public.account_assessment where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('account_assessment', v_n);
    delete from public.person_observation where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('person_observation', v_n);

    -- Replay summaries keep other clients' entries; the totals are marked stale.
    update public.replay_run
       set summary = jsonb_set(summary, '{clients}',
                       coalesce((select jsonb_agg(e) from jsonb_array_elements(summary -> 'clients') e
                                  where e ->> 'client_id' is distinct from p_client_id::text), '[]'::jsonb))
                     || jsonb_build_object('stale', true)
     where workspace_id = v_workspace_id
       and jsonb_typeof(summary -> 'clients') = 'array'
       and exists (select 1 from jsonb_array_elements(summary -> 'clients') e
                    where e ->> 'client_id' = p_client_id::text);

    -- The client's people (names, addresses); firm-side people never belong to a client.
    select coalesce(array_agg(id), '{}') into v_people
      from public.person where workspace_id = v_workspace_id and client_id = p_client_id and side = 'client';
    -- Records of other clients may list them as participants (no foreign key on the array).
    update public.source_record
       set participants = array(select x from unnest(participants) x where x <> all(v_people))
     where workspace_id = v_workspace_id and participants && v_people;
    delete from public.person where workspace_id = v_workspace_id and id = any(v_people);
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('person', v_n);

  elsif p_policy = 'metadata_only' then
    update public.source_record
       set body_clean = null, raw_object_uri = null, policy_at_ingest = 'metadata_only',
           body_purged_at = coalesce(body_purged_at, now())
     where workspace_id = v_workspace_id and client_id = p_client_id and policy_at_ingest = 'full';
    get diagnostics v_affected = row_count;

    -- Nothing left to process for these records.
    update public.source_record
       set processing_state = 'not_applicable'
     where workspace_id = v_workspace_id and client_id = p_client_id
       and processing_state in ('pending', 'error');

    -- Everything derived from bodies, quotes included. Extractions first (their links cascade),
    -- then evidence (which cascades every remaining evidence link).
    delete from public.extraction where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('extraction', v_n);
    delete from public.evidence where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('evidence', v_n);
    delete from public.commitment where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('commitment', v_n);
    delete from public.client_goal where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('client_goal', v_n);
    delete from public.request where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('request', v_n);
    delete from public.latent_need where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('latent_need', v_n);
    delete from public.decision_card where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('decision_card', v_n);
    delete from public.client_card where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('client_card', v_n);
    -- Metadata signals (volume, latency, meetings, dates) stay: they hold no quotes.
    delete from public.signal
     where workspace_id = v_workspace_id and client_id = p_client_id and basis = 'evidence';
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('signal', v_n);
    delete from public.account_assessment where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('account_assessment', v_n);
    delete from public.person_observation
     where workspace_id = v_workspace_id and client_id = p_client_id and source <> 'manual';
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('person_observation', v_n);

    -- Titles and decision-maker flags learned from bodies must not survive.
    update public.person p
       set role_title = (select o.role_title from public.person_observation o
                          where o.workspace_id = p.workspace_id and o.person_id = p.id and o.role_title is not null
                          order by o.observed_at desc limit 1),
           is_decision_maker = exists (select 1 from public.person_observation o
                                        where o.workspace_id = p.workspace_id and o.person_id = p.id
                                          and o.is_decision_maker)
     where p.workspace_id = v_workspace_id and p.client_id = p_client_id and p.side = 'client';
  end if;

  perform private.write_audit(v_workspace_id, 'client.policy_changed', 'client', p_client_id,
    jsonb_build_object('from', v_previous, 'to', p_policy, 'records_affected', v_affected,
                       'derived_deleted', v_deleted));
end;
$$;
