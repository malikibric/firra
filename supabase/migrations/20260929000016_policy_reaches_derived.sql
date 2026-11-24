-- P2 D15: a client's data policy reaches everything derived from its records, not just the records.
--   excluded      -> records, every derived row, and the client's people are deleted.
--   metadata_only -> everything derived from bodies (extractions, verified quotes, memory, cards) is
--                    deleted; metadata-based signals and the people stay.
-- Before this, commitments/goals/needs/signals/cards survived exclusion (hidden by RLS only) and a
-- metadata_only client kept every stored quote.

-- Defense in depth: quotes can only be stored for a full-policy client and a full-policy record.
-- The share lock serializes with set_client_policy (whose UPDATE takes the client row lock), so an
-- insert racing a policy change either is seen by the deletes or is rejected.
create function private.evidence_requires_full_policy()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_client public.data_policy;
  v_record public.data_policy;
  v_record_client uuid;
begin
  select c.policy, r.policy_at_ingest, r.client_id into v_client, v_record, v_record_client
    from public.client c
    left join public.source_record r on r.workspace_id = c.workspace_id and r.id = new.source_record_id
   where c.workspace_id = new.workspace_id and c.id = new.client_id
     for share of c;
  -- A missing client or a record of another client is the foreign keys' business (23503).
  if not found or v_record_client is distinct from new.client_id then
    return new;
  end if;
  if v_client <> 'full' or v_record <> 'full' then
    raise exception 'evidence requires a full-policy client' using errcode = '23514';
  end if;
  return new;
end;
$$;

create trigger evidence_requires_full_policy before insert on public.evidence
  for each row execute function private.evidence_requires_full_policy();

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
  end if;

  perform private.write_audit(v_workspace_id, 'client.policy_changed', 'client', p_client_id,
    jsonb_build_object('from', v_previous, 'to', p_policy, 'records_affected', v_affected,
                       'derived_deleted', v_deleted));
end;
$$;
