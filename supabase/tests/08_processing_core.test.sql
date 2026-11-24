-- P2 schema part 1: service catalog RPCs, processing state, evidence and extraction integrity,
-- read-path isolation, and LLM budget events.
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as
select tests.create_user('owner-a@example.com') as a, tests.create_user('owner-b@example.com') as b;
grant select on fx to authenticated;
create temp table t (k text primary key, v uuid) on commit drop;
grant all on t to authenticated;

select tests.authenticate_as((select a from fx));
insert into t select 'ws_a', public.create_workspace('A');
insert into t select 'acme', public.create_client((select v from t where k = 'ws_a'), 'Acme');
insert into t select 'globex', public.create_client((select v from t where k = 'ws_a'), 'Globex');
select tests.authenticate_as((select b from fx));
insert into t select 'ws_b', public.create_workspace('B');

-- Service catalog ------------------------------------------------------------------------------
select tests.authenticate_as((select a from fx));
insert into t select 'svc', public.save_workspace_service((select v from t where k = 'ws_a'), null,
  ' SEO ', 'Search engine optimization', array[' Backlinks ', 'backlinks', 'audit', '']);
select results_eq($$select name, keywords from public.workspace_service$$,
  $$values ('SEO'::text, array['backlinks', 'audit']::text[])$$,
  'service is trimmed, keywords are normalized and de-duplicated in order');
select lives_ok(format($$select public.save_workspace_service(%L, %L, 'SEO & content', 'x', array['seo'])$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'svc')), 'owner updates a service');
select is((select name from public.workspace_service), 'SEO & content', 'service updated');
select throws_ok(format($$select public.save_workspace_service(%L, null, 'seo & CONTENT')$$, (select v from t where k = 'ws_a')),
  '23505', null, 'service names are unique per workspace, case-insensitive');
select throws_ok(format($$select public.save_workspace_service(%L, %L, 'x')$$,
  (select v from t where k = 'ws_a'), gen_random_uuid()), 'P0002', 'service not found', 'unknown service id rejected');
select throws_ok(format($$select public.save_workspace_service(%L, null, '')$$, (select v from t where k = 'ws_a')),
  '23514', null, 'empty name rejected');

select tests.authenticate_as((select b from fx));
select is((select count(*)::int from public.workspace_service), 0, 'B cannot see A services');
select throws_ok(format($$select public.save_workspace_service(%L, null, 'x')$$, (select v from t where k = 'ws_a')),
  '42501', 'forbidden', 'B cannot create services in A');
select throws_ok(format($$select public.delete_workspace_service(%L)$$, (select v from t where k = 'svc')),
  '42501', 'forbidden', 'B cannot delete A services');
select throws_ok($$insert into public.workspace_service (workspace_id, name) select id, 'x' from public.workspace$$,
  '42501', null, 'services cannot be written directly');

select tests.authenticate_as((select a from fx));
select is((select count(*)::int from public.audit_log where action like 'service.%'), 2, 'create and update are audited');
select lives_ok(format($$select public.delete_workspace_service(%L)$$, (select v from t where k = 'svc')), 'owner deletes a service');
select is((select count(*)::int from public.workspace_service), 0, 'service deleted');
select is((select count(*)::int from public.audit_log where action = 'service.deleted'), 1, 'delete is audited');

-- Fixtures written by the worker (service role) --------------------------------------------------
select tests.as_postgres();
with c as (
  insert into public.connection (workspace_id, provider, mode, account_label)
  values ((select v from t where k = 'ws_a'), 'email_upload', 'upload', 'export.mbox') returning id)
insert into t select 'conn', id from c;
with r as (
  insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id, policy_at_ingest,
                                    occurred_at, direction, word_count, body_clean)
  values ((select v from t where k = 'ws_a'), (select v from t where k = 'conn'), 'email_upload', 'm-1', 'email',
          (select v from t where k = 'acme'), 'full', now(), 'in', 12, 'Hello, we are two weeks late on the report.')
  returning id, processing_state)
insert into t select 'rec', id from r;
select is((select processing_state::text from public.source_record where id = (select v from t where k = 'rec')),
  'not_applicable', 'new records default to not_applicable until ingest marks them pending');

-- Evidence and extraction integrity ---------------------------------------------------------------
select throws_ok(format($$insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
  values (%L, %L, %L, 'two words', '{"start":0,"end":9}')$$, (select v from t where k = 'ws_a'),
  (select v from t where k = 'rec'), (select v from t where k = 'acme')),
  '23514', null, 'quotes under 3 words rejected');
select throws_ok(format($$insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
  values (%L, %L, %L, %L, '{"start":0,"end":9}')$$, (select v from t where k = 'ws_a'),
  (select v from t where k = 'rec'), (select v from t where k = 'acme'),
  array_to_string(array(select 'w' || n from generate_series(1, 26) n), ' ')),
  '23514', null, 'quotes over 25 words rejected');
select throws_ok(format($$insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
  values (%L, %L, %L, 'we are two weeks late', '{"start":0,"end":21}')$$, (select v from t where k = 'ws_a'),
  (select v from t where k = 'rec'), (select v from t where k = 'globex')),
  '23503', null, 'evidence must carry the client of its record');

select lives_ok(format($sql$
  do $do$
  declare e uuid; v uuid;
  begin
    insert into public.extraction (workspace_id, source_record_id, client_id, kind, payload, model, prompt_version,
                                   processing_version, item_index)
    values (%1$L, %2$L, %3$L, 'complaint', '{"subtype":"delay"}', 'm', 'extract.v1', 1, 0) returning id into e;
    insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
    values (%1$L, %2$L, %3$L, 'we are two weeks late', '{"start":7,"end":28}') returning id into v;
    insert into public.extraction_evidence (workspace_id, extraction_id, evidence_id, client_id)
    values (%1$L, e, v, %3$L);
    set constraints extraction_requires_evidence immediate;
  end $do$ $sql$, (select v from t where k = 'ws_a'), (select v from t where k = 'rec'), (select v from t where k = 'acme')),
  'extraction with evidence commits');

select throws_ok(format($sql$
  do $do$
  begin
    insert into public.extraction (workspace_id, source_record_id, client_id, kind, payload, model, prompt_version,
                                   processing_version, item_index)
    values (%1$L, %2$L, %3$L, 'praise', '{}', 'm', 'extract.v1', 1, 1);
    set constraints extraction_requires_evidence immediate;
  end $do$ $sql$, (select v from t where k = 'ws_a'), (select v from t where k = 'rec'), (select v from t where k = 'acme')),
  '23514', 'extraction requires evidence', 'extraction without evidence is rejected');
select throws_ok(format($$insert into public.extraction (workspace_id, source_record_id, client_id, kind, payload, model, prompt_version,
    processing_version, item_index)
  values (%L, %L, %L, 'complaint', '{}', 'm', 'extract.v1', 1, 0)$$, (select v from t where k = 'ws_a'),
  (select v from t where k = 'rec'), (select v from t where k = 'acme')),
  '23505', null, 'a retried run cannot duplicate an item');
select throws_ok(format($$insert into public.extraction (workspace_id, source_record_id, client_id, kind, payload, model, prompt_version,
    processing_version, item_index)
  values (%L, %L, %L, 'complaint', '{}', 'm', 'extract.v1', 1, 5)$$, (select v from t where k = 'ws_a'),
  (select v from t where k = 'rec'), (select v from t where k = 'globex')),
  '23503', null, 'extraction must carry the client of its record');

-- Read path ------------------------------------------------------------------------------------------
select tests.authenticate_as((select a from fx));
select is((select count(*)::int from public.extraction), 1, 'member sees extractions');
select is((select count(*)::int from public.evidence), 1, 'member sees evidence');
select is((select count(*)::int from public.extraction_evidence), 1, 'member sees extraction evidence links');
select throws_ok($$insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
  select workspace_id, id, client_id, 'we are two weeks late', '{}' from public.source_record$$,
  '42501', null, 'evidence cannot be written by users');
select throws_ok($$delete from public.extraction$$, '42501', null, 'extractions cannot be deleted by users');

select tests.authenticate_as((select b from fx));
select is((select count(*)::int from public.extraction), 0, 'B cannot see A extractions');
select is((select count(*)::int from public.evidence), 0, 'B cannot see A evidence');
select is((select count(*)::int from public.extraction_evidence), 0, 'B cannot see A evidence links');

-- Excluding a client removes the derived rows with the records.
select tests.authenticate_as((select a from fx));
select lives_ok(format($$select public.set_client_policy(%L, 'excluded')$$, (select v from t where k = 'acme')), 'exclude acme');
select tests.as_postgres();
select is((select count(*)::int from public.extraction), 0, 'excluded client extractions deleted');
select is((select count(*)::int from public.evidence), 0, 'excluded client evidence deleted');
select is((select count(*)::int from public.extraction_evidence), 0, 'excluded client evidence links deleted');

-- LLM budget events ------------------------------------------------------------------------------------
select lives_ok(format($$insert into public.llm_budget_event (workspace_id, month, threshold) values (%L, '2026-09-01', 50)$$,
  (select v from t where k = 'ws_a')), 'budget event stored');
select throws_ok(format($$insert into public.llm_budget_event (workspace_id, month, threshold) values (%L, '2026-09-01', 50)$$,
  (select v from t where k = 'ws_a')), '23505', null, 'a threshold is stored once per month');
select throws_ok(format($$insert into public.llm_budget_event (workspace_id, month, threshold) values (%L, '2026-09-01', 70)$$,
  (select v from t where k = 'ws_a')), '23514', null, 'only 50, 80 and 100 are valid thresholds');
select throws_ok(format($$insert into public.llm_budget_event (workspace_id, month, threshold) values (%L, '2026-09-15', 80)$$,
  (select v from t where k = 'ws_a')), '23514', null, 'month must be the first day');
select tests.authenticate_as((select a from fx));
select is((select count(*)::int from public.llm_budget_event), 1, 'owner reads budget events');
select tests.authenticate_as((select b from fx));
select is((select count(*)::int from public.llm_budget_event), 0, 'B cannot read A budget events');

select * from finish();
rollback;
