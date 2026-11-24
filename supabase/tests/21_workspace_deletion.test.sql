-- Workspace hard deletion: catalog-wide cascade paths, populated chains and tenant isolation.
-- All fixtures and helpers are transaction-local; no production schema or policies are changed.
begin;
\ir _helpers.psql
\ir _derived_helpers.psql
select no_plan();

-- Discover base tables, not views (which may hide surviving rows through RLS/policy filters).
create temp table tenant_tables on commit drop as
select c.table_name, cl.oid as table_oid, a.attnum as workspace_attnum
  from information_schema.columns c
  join information_schema.tables t using (table_schema, table_name)
  join pg_namespace n on n.nspname = c.table_schema
  join pg_class cl on cl.relnamespace = n.oid and cl.relname = c.table_name
  join pg_attribute a on a.attrelid = cl.oid and a.attname = c.column_name
 where c.table_schema = 'public' and c.column_name = 'workspace_id' and t.table_type = 'BASE TABLE';
select ok(exists (select 1 from tenant_tables), 'workspace-scoped public tables were discovered');

-- Follow only validated CASCADE FKs that map workspace_id to the parent's tenant key.
-- Nullable composite keys do not guarantee a parent exists (MATCH SIMPLE can skip that FK).
-- UNION terminates cycles; a disconnected cycle cannot manufacture a path to workspace(id).
create temp table cascade_tables on commit drop as
with recursive cascade_edges as (
  select fk.conrelid as child_oid, child.attnum as child_attnum,
         fk.confrelid as parent_oid, parent.attnum as parent_attnum
    from pg_constraint fk
    cross join lateral unnest(fk.conkey, fk.confkey) as keys(child_key, parent_key)
    join pg_attribute child on child.attrelid = fk.conrelid and child.attnum = keys.child_key
    join pg_attribute parent on parent.attrelid = fk.confrelid and parent.attnum = keys.parent_key
   where fk.contype = 'f' and fk.confdeltype = 'c' and fk.convalidated
     and child.attname = 'workspace_id'
     and not exists (
       select 1 from unnest(fk.conkey) k
         join pg_attribute a on a.attrelid = fk.conrelid and a.attnum = k
        where not a.attnotnull)
), reachable(table_oid, tenant_attnum) as (
  select 'public.workspace'::regclass::oid, attnum
    from pg_attribute where attrelid = 'public.workspace'::regclass and attname = 'id'
  union
  select e.child_oid, e.child_attnum from cascade_edges e
    join reachable r on e.parent_oid = r.table_oid and e.parent_attnum = r.tenant_attnum
)
select table_oid, tenant_attnum from reachable;

select ok(exists (select 1 from cascade_tables c
                  where c.table_oid = t.table_oid and c.tenant_attnum = t.workspace_attnum),
          format('public.%I has a workspace-key ON DELETE CASCADE path to workspace(id)', t.table_name))
  from tenant_tables t order by t.table_name;
select is_empty($$
  select t.table_name from tenant_tables t
   where not exists (select 1 from cascade_tables c
                      where c.table_oid = t.table_oid and c.tenant_attnum = t.workspace_attnum)
   order by t.table_name
$$, 'cascade offenders by table name (must be empty)');

-- Populate both workspaces identically through the main chain and every current tenant table.
-- The dynamic preconditions below fail if a future table is added without a populated fixture.
create function tests.seed_deletion_workspace(p_label text)
returns uuid
language plpgsql
as $$
declare
  ws uuid;
  owner_id uuid := tests.create_user(p_label || '@deletion.example.com');
  client_id uuid;
  person_id uuid;
  connection_id uuid;
  record_id uuid;
  evidence_id uuid;
  extraction_id uuid;
  parent_id uuid;
  need_id uuid;
  request_id uuid;
  signal_id uuid;
  v_assessment_id uuid;
  replay_id uuid;
  routine_id uuid;
  run_id uuid;
  card_id uuid;
  job_id uuid;
  event_id uuid;
  brief_id uuid;
  service_id uuid;
  memory_table text;
begin
  insert into public.workspace (name) values (p_label) returning id into ws;
  insert into public.membership (workspace_id, user_id, role) values (ws, owner_id, 'owner');
  insert into public.workspace_invite (workspace_id, email, token_hash, invited_by, expires_at)
  values (ws, 'invite@deletion.example.com', sha256(convert_to(p_label || '-invite', 'UTF8')),
          owner_id, now() + interval '1 day');
  insert into public.workspace_key (workspace_id, wrapped_dek, kek_name) values (ws, '\x01', 'synthetic');
  insert into public.workspace_service (workspace_id, name) values (ws, 'Synthetic service') returning id into service_id;
  insert into public.client (workspace_id, name) values (ws, 'Synthetic client') returning id into client_id;
  insert into public.client_domain (workspace_id, client_id, domain) values (ws, client_id, 'deletion.example.com');
  insert into public.contributor (workspace_id, email, invited_by) values (ws, 'contributor@deletion.example.com', owner_id);
  insert into public.person (workspace_id, client_id, side) values (ws, client_id, 'client') returning id into person_id;
  insert into public.person_identity (workspace_id, person_id, kind, value)
  values (ws, person_id, 'email', 'client@deletion.example.com');
  insert into public.connection (workspace_id, provider, mode, account_label, owner_user_id, external_account_id)
  values (ws, 'gmail', 'user', 'Synthetic mailbox', owner_id, p_label) returning id into connection_id;
  insert into public.connection_scope (workspace_id, connection_id, kind, external_id, client_id)
  values (ws, connection_id, 'user', 'synthetic', client_id);
  insert into public.oauth_state (workspace_id, state_hash, provider, owner_user_id, code_verifier, return_to)
  values (ws, sha256(convert_to(p_label || '-oauth', 'UTF8')), 'google', owner_id, repeat('x', 43), '/connect');
  insert into public.job (workspace_id, type, idempotency_key, connection_id)
  values (ws, 'routines.run', 'deletion-proof', connection_id) returning id into job_id;
  insert into public.llm_usage (workspace_id, job_id, model, input_tokens, output_tokens, cost_usd)
  values (ws, job_id, 'synthetic', 1, 1, 0);
  insert into public.llm_budget_event (workspace_id, month, threshold) values (ws, '2026-10-01', 50);
  insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id,
    policy_at_ingest, occurred_at, author_person_id, direction, body_clean, vault_user_id)
  values (ws, connection_id, 'gmail', 'synthetic-record', 'email', client_id, 'full', now(), person_id,
          'in', 'Please send the report', owner_id) returning id into record_id;
  insert into public.calendar_event (workspace_id, source_record_id, client_id, starts_at, duration_min, status, attendee_count)
  values (ws, record_id, client_id, now(), 30, 'scheduled', 2) returning id into event_id;
  insert into public.person_observation (workspace_id, client_id, person_id, observed_at, source, source_record_id)
  values (ws, client_id, person_id, now(), 'signature', record_id);
  insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
  values (ws, record_id, client_id, 'Please send the report', '{"start":0,"end":22}') returning id into evidence_id;
  insert into public.extraction (workspace_id, source_record_id, client_id, kind, payload, model,
    prompt_version, processing_version, item_index)
  values (ws, record_id, client_id, 'request', '{"text":"Please send the report"}', 'synthetic', 'extract.v5', 1, 0)
  returning id into extraction_id;
  insert into public.extraction_evidence (workspace_id, extraction_id, evidence_id, client_id)
  values (ws, extraction_id, evidence_id, client_id);
  foreach memory_table in array array['commitment', 'client_goal', 'request', 'latent_need', 'signal'] loop
    parent_id := tests.insert_parent(memory_table, ws, client_id, record_id, 'deletion-proof');
    perform tests.link_evidence(memory_table, ws, client_id, parent_id, evidence_id);
    if memory_table = 'request' then request_id := parent_id; end if;
    if memory_table = 'latent_need' then need_id := parent_id; end if;
    if memory_table = 'signal' then signal_id := parent_id; end if;
  end loop;
  update public.latent_need set workspace_service_id = service_id where id = need_id;
  insert into public.latent_need_request (workspace_id, latent_need_id, request_id, client_id)
  values (ws, need_id, request_id, client_id);
  insert into public.client_card (workspace_id, client_id, version, content) values (ws, client_id, 1, '{}');
  insert into public.signal_event (workspace_id, client_id, signal_id, change, severity, as_of, rule_version, job_id)
  values (ws, client_id, signal_id, 'opened', 'high', now(), 'v1', job_id);
  insert into public.replay_run (workspace_id, window_start, window_end, requested_by, job_id)
  values (ws, '2026-09-01', '2026-10-01', owner_id, job_id) returning id into replay_id;
  insert into public.replay_narrative (workspace_id, replay_run_id, client_id, version, narrative)
  values (ws, replay_id, client_id, 1, '{}');
  insert into public.account_assessment (workspace_id, client_id, as_of, run_kind, replay_run_id,
    assessment_version, rule_version, risk_state, state_since, expansion_state, strength)
  values (ws, client_id, '2026-10-01', 'replay', replay_id, 'assessment.v1', 'v1', 'high', '2026-10-01', 'none', 'strong')
  returning id into v_assessment_id;
  insert into public.assessment_evidence (workspace_id, assessment_id, evidence_id, client_id)
  values (ws, v_assessment_id, evidence_id, client_id);
  insert into public.assessment_signal (workspace_id, assessment_id, signal_id, client_id)
  values (ws, v_assessment_id, signal_id, client_id);
  insert into public.routine (workspace_id, kind) values (ws, 'morning_scan') returning id into routine_id;
  insert into public.routine_run (workspace_id, routine_id, scheduled_for, job_id)
  values (ws, routine_id, '2026-10-01', job_id) returning id into run_id;
  card_id := tests.insert_parent('decision_card', ws, client_id, record_id, 'deletion-proof');
  perform tests.link_evidence('decision_card', ws, client_id, card_id, evidence_id);
  update public.decision_card set assessment_id = v_assessment_id, routine_run_id = run_id,
    importance = 'important', surfaced_on = '2026-10-01', reason_code = 'risk_rose_high' where id = card_id;
  insert into public.decision_feedback (workspace_id, client_id, decision_card_id, user_id, verdict)
  values (ws, client_id, card_id, owner_id, 'useful');
  insert into public.outcome (workspace_id, client_id, event, occurred_at, recorded_by)
  values (ws, client_id, 'renewed', '2026-10-01', owner_id);
  insert into public.meeting_brief (workspace_id, calendar_event_id, client_id, starts_at, basis, content, version)
  values (ws, event_id, client_id, now(), 'evidence', '{}', 'facts.v1') returning id into brief_id;
  insert into public.meeting_brief_evidence (workspace_id, meeting_brief_id, evidence_id, client_id)
  values (ws, brief_id, evidence_id, client_id);
  insert into public.mcp_token (workspace_id, user_id, name, token_hash, token_prefix, expires_at)
  values (ws, owner_id, 'Synthetic token', sha256(convert_to(p_label || '-mcp', 'UTF8')), 'frm_00000000',
          now() + interval '1 day');
  insert into public.audit_log (workspace_id, actor_type, actor_id, action, target_type, target_id)
  values (ws, 'user', owner_id, 'workspace.deletion_proof', 'client', client_id);
  return ws;
end;
$$;

create temp table fixture on commit drop as
select tests.seed_deletion_workspace('delete-' || gen_random_uuid()) as doomed,
       tests.seed_deletion_workspace('keep-' || gen_random_uuid()) as kept;
-- Validate evidence constraints before deletion; rollback must not hide invalid fixtures.
set constraints all immediate;

create function tests.workspace_rows(p_table text, p_workspace uuid)
returns jsonb
language plpgsql
as $$
declare rows jsonb;
begin
  execute format('select coalesce(jsonb_agg(to_jsonb(r) order by r.id), ''[]''::jsonb)
                    from public.%I r where r.workspace_id = $1', p_table) into rows using p_workspace;
  return rows;
end;
$$;

create temp table before_delete on commit drop as
select table_name,
       tests.workspace_rows(table_name, (select doomed from fixture)) as doomed_rows,
       tests.workspace_rows(table_name, (select kept from fixture)) as kept_rows
  from tenant_tables;
select ok(jsonb_array_length(doomed_rows) > 0, format('public.%I has populated doomed fixtures', table_name))
  from before_delete order by table_name;
select ok(jsonb_array_length(kept_rows) > 0, format('public.%I has populated control fixtures', table_name))
  from before_delete order by table_name;
create temp table kept_workspace on commit drop as
select to_jsonb(w) as row from public.workspace w where id = (select kept from fixture);

-- Delete using the actual service_role, not a privileged SECURITY DEFINER test helper.
grant select on fixture to service_role;
set local role service_role;
select is(current_user::text, 'service_role', 'workspace deletion executes as service_role');
select lives_ok($$delete from public.workspace where id = (select doomed from fixture)$$,
                'service_role deletes the populated workspace without FK/trigger errors');
reset role;

select is((select count(*)::int from public.workspace where id = (select doomed from fixture)), 0,
          'the deleted workspace itself is gone');
select is((select to_jsonb(w) from public.workspace w where id = (select kept from fixture)),
          (select row from kept_workspace), 'the control workspace itself is unchanged');
-- Inspect physical rows as postgres, so RLS cannot turn a surviving row into a false pass.
select is(jsonb_array_length(tests.workspace_rows(table_name, (select doomed from fixture))), 0,
          format('public.%I has zero rows for the deleted workspace', table_name))
  from before_delete order by table_name;
select is(tests.workspace_rows(table_name, (select kept from fixture)), kept_rows,
          format('public.%I control rows are unchanged', table_name))
  from before_delete order by table_name;

select * from finish();
rollback;
