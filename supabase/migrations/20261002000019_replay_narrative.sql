-- Replay narratives as rows of their own (docs/plans/c-api-v1.md, T31b). They used to be planned as
-- `replay_run.summary.narratives`, but that column is readable by every member whatever the client's
-- policy and survives policy changes. A table with client-level RLS and a delete in
-- `set_client_policy` follows the same rules as every other derived row.

create table public.replay_narrative (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  replay_run_id uuid not null,
  client_id uuid not null,
  version integer not null check (version >= 1),
  -- Dates, enums, driver types, evidence ids and counts only; never text from records.
  narrative jsonb not null check (jsonb_typeof(narrative) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, replay_run_id)
    references public.replay_run (workspace_id, id) on delete cascade,
  foreign key (workspace_id, client_id)
    references public.client (workspace_id, id) on delete cascade,
  unique (replay_run_id, client_id)
);
create index replay_narrative_client_idx on public.replay_narrative (workspace_id, client_id);

create trigger replay_narrative_set_updated_at before update on public.replay_narrative
  for each row execute function private.set_updated_at();

alter table public.replay_narrative enable row level security;

create policy replay_narrative_select_member on public.replay_narrative
  for select to authenticated
  using (private.is_member(workspace_id) and private.client_visible(workspace_id, client_id));
grant select on public.replay_narrative to authenticated;

-- Same function as before, plus the narrative rows of the client in both branches (an exclusion
-- removes everything about the client, a metadata downgrade removes everything derived from bodies
-- including the assessments a narrative is built from).
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
    delete from public.replay_narrative where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('replay_narrative', v_n);
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
    delete from public.replay_narrative where workspace_id = v_workspace_id and client_id = p_client_id;
    get diagnostics v_n = row_count;
    v_deleted := v_deleted || jsonb_build_object('replay_narrative', v_n);
    -- Per-client replay totals were computed from the assessments that were just deleted: drop this
    -- client's entries and mark the totals stale, exactly as an exclusion does.
    update public.replay_run
       set summary = jsonb_set(summary, '{clients}',
                       coalesce((select jsonb_agg(e) from jsonb_array_elements(summary -> 'clients') e
                                  where e ->> 'client_id' is distinct from p_client_id::text), '[]'::jsonb))
                     || jsonb_build_object('stale', true)
     where workspace_id = v_workspace_id
       and jsonb_typeof(summary -> 'clients') = 'array'
       and exists (select 1 from jsonb_array_elements(summary -> 'clients') e
                    where e ->> 'client_id' = p_client_id::text);

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
