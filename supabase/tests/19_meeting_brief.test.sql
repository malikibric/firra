-- T45: meeting briefs. Isolation, hidden excluded clients, evidence or nothing, no writes by users,
-- and the policy trigger that drops briefs when a client leaves `full`.
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
insert into t select 'other', public.create_client((select v from t where k = 'ws_b'), 'Other');

-- Fixtures written by the worker (service role) --------------------------------------------------
select tests.as_postgres();
with c as (
  insert into public.connection (workspace_id, provider, mode, account_label)
  values ((select v from t where k = 'ws_a'), 'email_upload', 'upload', 'export.mbox') returning id)
insert into t select 'conn', id from c;
with r as (
  insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id,
                                    policy_at_ingest, occurred_at, direction, word_count, body_clean)
  values ((select v from t where k = 'ws_a'), (select v from t where k = 'conn'), 'email_upload', 'm-1', 'email',
          (select v from t where k = 'acme'), 'full', now(), 'in', 12, 'We are two weeks late on the report.'),
         ((select v from t where k = 'ws_a'), (select v from t where k = 'conn'), 'email_upload', 'cal-acme', 'calendar_event',
          (select v from t where k = 'acme'), 'full', now(), 'internal', 0, null),
         ((select v from t where k = 'ws_a'), (select v from t where k = 'conn'), 'email_upload', 'cal-globex', 'calendar_event',
          (select v from t where k = 'globex'), 'full', now(), 'internal', 0, null)
  returning id, external_id)
insert into t select case external_id when 'm-1' then 'rec' when 'cal-acme' then 'rec_cal_acme' else 'rec_cal_globex' end, id from r;
with e as (
  insert into public.calendar_event (workspace_id, source_record_id, client_id, starts_at, duration_min, status, attendee_count)
  values ((select v from t where k = 'ws_a'), (select v from t where k = 'rec_cal_acme'), (select v from t where k = 'acme'),
          now() + interval '1 hour', 30, 'scheduled', 3),
         ((select v from t where k = 'ws_a'), (select v from t where k = 'rec_cal_globex'), (select v from t where k = 'globex'),
          now() + interval '2 hours', 30, 'scheduled', 2)
  returning id, client_id, starts_at)
insert into t select case when client_id = (select v from t where k = 'acme') then 'ev_cal_acme' else 'ev_cal_globex' end, id from e;
with ev as (
  insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
  values ((select v from t where k = 'ws_a'), (select v from t where k = 'rec'), (select v from t where k = 'acme'),
          'we are two weeks late', '{"start":3,"end":24}') returning id)
insert into t select 'evidence', id from ev;

create function tests.add_brief(p_event text, p_client text, p_basis public.signal_basis)
returns uuid
language sql
as $$
  insert into public.meeting_brief (workspace_id, calendar_event_id, client_id, starts_at, basis, content, version)
  select ce.workspace_id, ce.id, ce.client_id, ce.starts_at, p_basis, '{"questions":[]}', 'facts.v1'
    from public.calendar_event ce where ce.id = (select v from t where k = p_event)
  returning id;
$$;

-- Evidence or nothing -----------------------------------------------------------------------------
select throws_ok($$
  do $do$ begin
    perform tests.add_brief('ev_cal_acme', 'acme', 'evidence');
    set constraints all immediate;
  end $do$ $$, '23514', null, 'an evidence brief without a cited quote is rejected at commit');
select lives_ok($$
  do $do$
  declare b uuid;
  begin
    set constraints all deferred;
    b := tests.add_brief('ev_cal_acme', 'acme', 'evidence');
    insert into public.meeting_brief_evidence (workspace_id, meeting_brief_id, evidence_id, client_id)
    values ((select v from t where k = 'ws_a'), b, (select v from t where k = 'evidence'), (select v from t where k = 'acme'));
    set constraints all immediate;
  end $do$ $$, 'an evidence brief with a cited quote is accepted');
select lives_ok($$
  do $do$ begin
    perform tests.add_brief('ev_cal_globex', 'globex', 'metadata');
    set constraints all immediate;
  end $do$ $$, 'a metadata brief needs no evidence');

-- Links stay inside one client and one workspace ----------------------------------------------------
select throws_ok(format($$
  insert into public.meeting_brief (workspace_id, calendar_event_id, client_id, starts_at, basis, content, version)
  select ce.workspace_id, ce.id, %L, ce.starts_at + interval '1 day', 'metadata', '{}', 'facts.v1'
    from public.calendar_event ce where ce.id = %L $$,
  (select v from t where k = 'globex'), (select v from t where k = 'ev_cal_acme')),
  '23503', null, 'a brief cannot name a client other than its meeting''s');
select throws_ok($$
  insert into public.meeting_brief (workspace_id, calendar_event_id, client_id, starts_at, basis, content, version)
  select ce.workspace_id, ce.id, ce.client_id, ce.starts_at, 'metadata', '{}', 'facts.v1'
    from public.calendar_event ce where ce.id = (select v from t where k = 'ev_cal_acme') $$,
  '23505', null, 'one brief per meeting and start time');

-- Isolation and read-only -----------------------------------------------------------------------
select tests.authenticate_as((select a from fx));
select is((select count(*)::int from public.meeting_brief), 2, 'the owner reads the briefs of their clients');
select is((select count(*)::int from public.meeting_brief_evidence), 1, 'the owner reads the evidence links');
select tests.authenticate_as((select b from fx));
select is((select count(*)::int from public.meeting_brief), 0, 'another workspace sees no briefs');
select is((select count(*)::int from public.meeting_brief_evidence), 0, 'another workspace sees no links');
select throws_ok($$insert into public.meeting_brief (workspace_id, calendar_event_id, client_id, starts_at, basis, content, version)
  values (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), now(), 'metadata', '{}', 'x')$$,
  '42501', null, 'users cannot write briefs');
select tests.as_anon();
select throws_ok($$select count(*) from public.meeting_brief$$, '42501', null, 'anon cannot read briefs');

-- Excluded clients are hidden on read ---------------------------------------------------------------
select tests.as_postgres();
update public.client set policy = 'excluded' where id = (select v from t where k = 'globex');
select tests.authenticate_as((select a from fx));
select is((select count(*)::int from public.meeting_brief where client_id = (select v from t where k = 'globex')), 0,
  'briefs of an excluded client are not readable');

-- Leaving `full` drops the briefs --------------------------------------------------------------------
select tests.as_postgres();
select is((select count(*)::int from public.meeting_brief where client_id = (select v from t where k = 'globex')), 0,
  'excluding a client dropped its brief');
select is((select count(*)::int from public.meeting_brief where client_id = (select v from t where k = 'acme')), 1,
  'the other client keeps its brief');
update public.client set policy = 'metadata_only' where id = (select v from t where k = 'acme');
select is((select count(*)::int from public.meeting_brief where client_id = (select v from t where k = 'acme')), 0,
  'metadata_only drops the briefs that may hold quotes');
select is((select count(*)::int from public.meeting_brief_evidence), 0, 'and their evidence links');
select lives_ok($$
  do $do$ begin
    perform tests.add_brief('ev_cal_acme', 'acme', 'metadata');
    set constraints all immediate;
  end $do$ $$, 'a metadata brief can be rebuilt for the restricted client');
update public.client set policy = 'full' where id = (select v from t where k = 'acme');
select is((select count(*)::int from public.meeting_brief where client_id = (select v from t where k = 'acme')), 1,
  'moving back to full keeps the metadata brief until the next sweep replaces it');

select * from finish();
rollback;
