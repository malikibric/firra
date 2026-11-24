-- P2b operator core: signal history, assessments, replay runs, observations, structured cards,
-- feedback, outcomes and the policy extension (the design notes).
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as
select tests.create_user('owner-a@example.com') as a, tests.create_user('owner-b@example.com') as b;
grant select on fx to authenticated;
create temp table t (k text primary key, v uuid) on commit drop;
grant all on t to authenticated;

\ir _derived_helpers.psql

select tests.authenticate_as((select a from fx));
insert into t select 'ws_a', public.create_workspace('A');
insert into t select 'acme', public.create_client((select v from t where k = 'ws_a'), 'Acme');
insert into t select 'globex', public.create_client((select v from t where k = 'ws_a'), 'Globex');
select tests.authenticate_as((select b from fx));
insert into t select 'ws_b', public.create_workspace('B');
insert into t select 'other', public.create_client((select v from t where k = 'ws_b'), 'Other');

select tests.as_postgres();

-- Seeds one client with every P2b row through the service role; returns the client's person.
create function tests.seed_operator(p_ws uuid, p_client uuid, p_tag text, p_run uuid)
returns uuid
language plpgsql
as $$
declare
  v_conn uuid; v_rec uuid; v_ev uuid; v_sig uuid; v_live uuid; v_replay uuid; v_person uuid; v_card uuid;
begin
  set constraints all deferred;
  insert into public.connection (workspace_id, provider, mode, account_label)
  values (p_ws, 'email_upload', 'upload', 'export-' || p_tag) returning id into v_conn;
  insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id,
                                    policy_at_ingest, occurred_at, direction, word_count, body_clean)
  values (p_ws, v_conn, 'email_upload', 'rec-' || p_tag, 'email', p_client, 'full', now(), 'in', 12,
          'We are two weeks late on the report.')
  returning id into v_rec;
  insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
  values (p_ws, v_rec, p_client, 'we are two weeks late', '{"start":3,"end":24}') returning id into v_ev;

  insert into public.signal (workspace_id, client_id, type, severity, occurred_at, rule_version, basis, inputs,
                             dedupe_key, family)
  values (p_ws, p_client, 'reduced_engagement', 'medium', now(), 'signals.v2', 'metadata', '{"messages_30":1}',
          'volume', 'engagement')
  returning id into v_sig;
  insert into public.signal_event (workspace_id, client_id, signal_id, change, severity, as_of, rule_version)
  values (p_ws, p_client, v_sig, 'opened', 'medium', now(), 'signals.v2');

  insert into public.account_assessment (workspace_id, client_id, as_of, run_kind, assessment_version, rule_version,
                                         risk_state, state_since, expansion_state, strength)
  values (p_ws, p_client, '2026-09-28', 'live', 'assessment.v1', 'signals.v2', 'watch', '2026-09-21', 'none', 'weak')
  returning id into v_live;
  insert into public.assessment_signal (workspace_id, assessment_id, signal_id, client_id)
  values (p_ws, v_live, v_sig, p_client);
  insert into public.assessment_evidence (workspace_id, assessment_id, evidence_id, client_id)
  values (p_ws, v_live, v_ev, p_client);
  insert into public.account_assessment (workspace_id, client_id, as_of, run_kind, replay_run_id, assessment_version,
                                         rule_version, risk_state, state_since, expansion_state, strength)
  values (p_ws, p_client, '2026-06-01', 'replay', p_run, 'assessment.v1', 'signals.v2', 'elevated', '2026-05-25',
          'none', 'moderate')
  returning id into v_replay;

  insert into public.person (workspace_id, client_id, side, display_name)
  values (p_ws, p_client, 'client', 'Person ' || p_tag) returning id into v_person;
  update public.person set role_title = 'VP Marketing', is_decision_maker = true where id = v_person;
  insert into public.person_observation (workspace_id, client_id, person_id, observed_at, source, role_title,
                                         is_decision_maker, source_record_id)
  values (p_ws, p_client, v_person, now() - interval '2 days', 'signature', 'VP Marketing', true, v_rec),
         (p_ws, p_client, v_person, now() - interval '9 days', 'manual', 'Marketing Manager', false, null);

  v_card := tests.insert_parent('decision_card', p_ws, p_client, v_rec, p_tag);
  perform tests.link_evidence('decision_card', p_ws, p_client, v_card, v_ev);
  update public.decision_card set assessment_id = v_live where id = v_card;

  insert into public.outcome (workspace_id, client_id, event, occurred_at, source)
  values (p_ws, p_client, 'renewed', '2026-03-01', 'user');
  set constraints all immediate;
  return v_person;
end;
$$;

select tests.as_postgres();
with r as (
  insert into public.replay_run (workspace_id, window_start, window_end, status, summary)
  values ((select v from t where k = 'ws_a'), '2026-04-01', '2026-09-28', 'succeeded',
          jsonb_build_object('clients', jsonb_build_array(
            jsonb_build_object('client_id', (select v from t where k = 'acme'), 'lead_days', 40),
            jsonb_build_object('client_id', (select v from t where k = 'globex'), 'lead_days', 12))))
  returning id)
insert into t select 'run_a', id from r;
with r as (
  insert into public.replay_run (workspace_id, window_start, window_end)
  values ((select v from t where k = 'ws_b'), '2026-04-01', '2026-09-28') returning id)
insert into t select 'run_b', id from r;
insert into t select 'p_acme', tests.seed_operator((select v from t where k = 'ws_a'), (select v from t where k = 'acme'), 'acme', (select v from t where k = 'run_a'));
insert into t select 'p_globex', tests.seed_operator((select v from t where k = 'ws_a'), (select v from t where k = 'globex'), 'globex', (select v from t where k = 'run_a'));
insert into t select 'p_other', tests.seed_operator((select v from t where k = 'ws_b'), (select v from t where k = 'other'), 'other', (select v from t where k = 'run_b'));

create temp table p2b (tbl text primary key) on commit drop;
grant select on p2b to authenticated;
insert into p2b values ('signal_event'), ('account_assessment'), ('assessment_evidence'), ('assessment_signal'),
  ('person_observation'), ('decision_feedback'), ('outcome');

-- Read path: members only, excluded clients hidden ------------------------------------------------
select tests.authenticate_as((select a from fx));
select ok(tests.count_rows(tbl, (select v from t where k = 'acme')) > 0, format('owner A sees acme %s', tbl))
from p2b where tbl <> 'decision_feedback';
select is((select count(*)::int from public.replay_run), 1, 'owner A sees only workspace A replay runs');
select tests.authenticate_as((select b from fx));
select is(tests.count_rows(tbl, (select v from t where k = 'acme')), 0, format('owner B sees no acme %s', tbl))
from p2b;
select is((select count(*)::int from public.replay_run where workspace_id = (select v from t where k = 'ws_a')), 0,
  'owner B sees no workspace A replay run');

select tests.as_postgres();
update public.client set policy = 'excluded' where id = (select v from t where k = 'acme');
select tests.authenticate_as((select a from fx));
select is(tests.count_rows(tbl, (select v from t where k = 'acme')), 0, format('an excluded client''s %s is hidden', tbl))
from p2b;
select tests.as_postgres();
update public.client set policy = 'full' where id = (select v from t where k = 'acme');

-- Signal history is append-only ---------------------------------------------------------------------
select throws_ok($$update public.signal_event set severity = 'high'$$, 'P0001', 'signal_event is append-only',
  'signal events cannot be updated');

-- Assessments: one live row per client and day, replay rows per run -------------------------------
select throws_ok(format($$insert into public.account_assessment (workspace_id, client_id, as_of, run_kind,
    assessment_version, rule_version, risk_state, state_since, expansion_state, strength)
  values (%L, %L, '2026-09-28', 'live', 'assessment.v1', 'signals.v2', 'high', '2026-09-28', 'none', 'weak')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23505', null, 'one live assessment per client and day');
select lives_ok(format($$
  with r as (insert into public.replay_run (workspace_id, window_start, window_end) values (%1$L, '2026-01-01', '2026-06-30')
             returning id)
  insert into public.account_assessment (workspace_id, client_id, as_of, run_kind, replay_run_id,
    assessment_version, rule_version, risk_state, state_since, expansion_state, strength)
  select %1$L, %2$L, '2026-06-01', 'replay', r.id, 'assessment.v1', 'signals.v2', 'watch', '2026-06-01', 'none', 'weak'
    from r$$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  'another replay run may assess the same client and day');
select throws_ok(format($$insert into public.account_assessment (workspace_id, client_id, as_of, run_kind,
    assessment_version, rule_version, risk_state, state_since, expansion_state, strength)
  values (%L, %L, '2026-06-08', 'replay', 'assessment.v1', 'signals.v2', 'watch', '2026-06-08', 'none', 'weak')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', null, 'a replay assessment needs its run');
select throws_ok(format($$insert into public.account_assessment (workspace_id, client_id, as_of, run_kind, replay_run_id,
    assessment_version, rule_version, risk_state, state_since, expansion_state, strength)
  values (%L, %L, '2026-09-29', 'live', %L, 'assessment.v1', 'signals.v2', 'watch', '2026-09-29', 'none', 'weak')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme'), (select v from t where k = 'run_a')),
  '23514', null, 'a live assessment has no run');
select throws_ok(format($$insert into public.account_assessment (workspace_id, client_id, as_of, run_kind, replay_run_id,
    assessment_version, rule_version, risk_state, state_since, expansion_state, strength)
  values (%L, %L, '2026-06-01', 'replay', %L, 'assessment.v1', 'signals.v2', 'watch', '2026-06-01', 'none', 'weak')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme'), (select v from t where k = 'run_b')),
  '23503', null, 'an assessment cannot use another workspace''s run');
select throws_ok(format($$insert into public.replay_run (workspace_id, window_start, window_end)
  values (%L, '2025-01-01', '2026-09-28')$$, (select v from t where k = 'ws_a')),
  '23514', null, 'a replay window is at most 400 days');

-- Observations cannot mix clients ---------------------------------------------------------------------
select throws_ok(format($$insert into public.person_observation (workspace_id, client_id, person_id, observed_at, source)
  values (%L, %L, %L, now(), 'manual')$$, (select v from t where k = 'ws_a'), (select v from t where k = 'globex'),
  (select v from t where k = 'p_acme')),
  '23503', null, 'an observation belongs to its person''s client');

-- Commitment closure times ------------------------------------------------------------------------------
select throws_ok(format($sql$
  do $do$ begin
    set constraints all deferred;
    insert into public.commitment (workspace_id, client_id, owner_side, text, stated_at, dedupe_key, status)
    values (%L, %L, 'firm', 'Send it', now(), 'done-open', 'done');
  end $do$ $sql$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', null, 'a done commitment needs closed_at');
select throws_ok(format($sql$
  do $do$ begin
    set constraints all deferred;
    insert into public.commitment (workspace_id, client_id, owner_side, text, stated_at, dedupe_key, status, closed_at)
    values (%L, %L, 'firm', 'Send it', now(), 'open-closed', 'open', now());
  end $do$ $sql$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', null, 'an open commitment has no closed_at');

-- Decision cards: evidence unless every fact is a metadata fact -----------------------------------
select lives_ok(format($sql$
  do $do$ begin
    set constraints all deferred;
    insert into public.decision_card (workspace_id, client_id, week, kind, title, body, locale, score, priority, inputs,
      model, prompt_version, dedupe_key, play, facts, interpretation, recommendation, strength)
    values (%L, %L, '2026-09-28', 'risk', 't', 'b', 'en', 1, 1, '{}', 'm', 'cards.v1', 'meta-only',
      'founder_review_call', '[{"id":"f1","text":"Client emails fell to 2 from 9","basis":"metadata"}]', 'i', 'r', 'weak');
    set constraints all immediate;
  end $do$ $sql$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  'a card of metadata facts commits without evidence');
select throws_ok(format($sql$
  do $do$ begin
    set constraints all deferred;
    insert into public.decision_card (workspace_id, client_id, week, kind, title, body, locale, score, priority, inputs,
      model, prompt_version, dedupe_key, play, facts, interpretation, recommendation, strength)
    values (%L, %L, '2026-09-28', 'risk', 't', 'b', 'en', 1, 1, '{}', 'm', 'cards.v1', 'quoted-bare',
      'fix_first', '[{"id":"f1","text":"Two late reports","basis":"evidence"}]', 'i', 'r', 'weak');
    set constraints all immediate;
  end $do$ $sql$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', 'decision_card requires evidence', 'a card with an evidence fact needs an evidence link');
select throws_ok(format($sql$
  do $do$ begin
    set constraints all deferred;
    insert into public.decision_card (workspace_id, client_id, week, kind, title, body, locale, score, priority, inputs,
      model, prompt_version, dedupe_key, play, facts, interpretation, recommendation, strength)
    values (%L, %L, '2026-09-28', 'risk', 't', 'b', 'en', 1, 1, '{}', 'm', 'cards.v1', 'no-basis',
      'fix_first', '[{"id":"f1","text":"Two late reports"}]', 'i', 'r', 'weak');
    set constraints all immediate;
  end $do$ $sql$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', 'decision_card facts need a basis of evidence or metadata', 'every card fact names its basis');
select throws_ok(format($sql$
  do $do$ begin
    set constraints all deferred;
    update public.decision_card
       set facts = '[{"id":"f1","text":"Two late reports","basis":"evidence"}]'
     where workspace_id = %L and dedupe_key = 'meta-only';
    set constraints all immediate;
  end $do$ $sql$, (select v from t where k = 'ws_a')),
  '23514', 'decision_card requires evidence', 'changing facts to an evidence claim needs an evidence link');

-- RPCs --------------------------------------------------------------------------------------------------
insert into t select 'card_acme', id from public.decision_card
 where client_id = (select v from t where k = 'acme') and dedupe_key = 'acme';
insert into t select 'admin', tests.create_user('admin-a@example.com');
insert into public.membership (workspace_id, user_id, role)
values ((select v from t where k = 'ws_a'), (select v from t where k = 'admin'), 'admin');

select tests.authenticate_as((select b from fx));
select throws_ok(format($$select public.record_decision_feedback(%L, 'useful')$$, (select v from t where k = 'card_acme')),
  '42501', null, 'another workspace cannot give feedback');
select throws_ok(format($$select public.record_client_outcome(%L, 'renewed', '2026-09-01')$$, (select v from t where k = 'acme')),
  '42501', null, 'another workspace cannot record outcomes');
select throws_ok(format($$select public.request_replay_run(%L, '2026-01-01', '2026-06-30')$$, (select v from t where k = 'ws_a')),
  '42501', null, 'another workspace cannot request a replay');

select tests.authenticate_as((select a from fx));
select lives_ok(format($$select public.record_decision_feedback(%L, 'useful', 'planned', 'Call on Monday')$$,
  (select v from t where k = 'card_acme')), 'owner records feedback');
select is((select status::text from public.decision_card where id = (select v from t where k = 'card_acme')), 'new',
  'useful + planned keeps the card open');
select lives_ok(format($$select public.record_decision_feedback(%L, 'useful', 'taken')$$,
  (select v from t where k = 'card_acme')), 'owner updates feedback');
select is((select count(*)::int from public.decision_feedback where decision_card_id = (select v from t where k = 'card_acme')), 1,
  'one feedback row per card and user');
select is((select status::text from public.decision_card where id = (select v from t where k = 'card_acme')), 'done',
  'action taken closes the card');
select lives_ok(format($$select public.record_decision_feedback(%L, 'wrong')$$, (select v from t where k = 'card_acme')),
  'owner marks the card wrong');
select is((select status::text from public.decision_card where id = (select v from t where k = 'card_acme')), 'dismissed',
  'a wrong card is dismissed');
select tests.authenticate_as((select v from t where k = 'admin'));
select lives_ok(format($$select public.record_decision_feedback(%L, 'already_known')$$,
  (select v from t where k = 'card_acme')), 'an admin records feedback');
select is((select count(*)::int from public.decision_feedback where decision_card_id = (select v from t where k = 'card_acme')), 2,
  'the admin sees both feedback rows of the workspace');
select tests.authenticate_as((select b from fx));
select is((select count(*)::int from public.decision_feedback), 0, 'another workspace sees no feedback');
select tests.authenticate_as((select a from fx));

insert into t select 'outcome_acme', public.record_client_outcome((select v from t where k = 'acme'), 'churned', '2026-09-15',
  '2026-08-01', '2026-07-20', -5000, 'usd', 'Budget moved in-house');
select is((select status::text || ' ' || churned_at::text from public.client where id = (select v from t where k = 'acme')),
  'churned 2026-09-15', 'a churned outcome marks the client churned');
select is((select currency::text from public.outcome where id = (select v from t where k = 'outcome_acme')), 'USD',
  'currency is normalized');
select throws_ok(format($$select public.record_client_outcome(%L, 'renewed', '2026-09-01', '2026-09-10')$$,
  (select v from t where k = 'globex')), '23514', null, 'notice cannot come after the outcome');
select lives_ok(format($$select public.delete_client_outcome(%L)$$, (select v from t where k = 'outcome_acme')),
  'owner deletes an outcome');
select is((select status::text || ' ' || coalesce(churned_at::text, '-') from public.client where id = (select v from t where k = 'acme')),
  'active -', 'deleting the only churn outcome undoes the churn it recorded');
select tests.authenticate_as((select v from t where k = 'admin'));
select lives_ok(format($$select public.record_client_outcome(%L, 'price_increase', '2026-09-20', null, null, 800, 'EUR')$$,
  (select v from t where k = 'globex')), 'an admin records an outcome');
select tests.authenticate_as((select a from fx));
insert into t select 'replay_req', public.request_replay_run((select v from t where k = 'ws_a'), '2026-03-01', '2026-09-28');
select is((select status::text from public.replay_run where id = (select v from t where k = 'replay_req')), 'queued',
  'a requested replay starts queued');

select tests.as_postgres();
select is((select count(*)::int from public.audit_log where workspace_id = (select v from t where k = 'ws_a')
            and action in ('decision_feedback.recorded', 'outcome.recorded', 'outcome.deleted', 'replay_run.requested')), 8,
  'every RPC call is audited');
select ok((select bool_and(metadata::text not like '%Call on Monday%' and metadata::text not like '%in-house%')
             from public.audit_log where workspace_id = (select v from t where k = 'ws_a')),
  'audit entries carry no free text');

-- Policy: metadata_only ---------------------------------------------------------------------------------
select tests.authenticate_as((select a from fx));
select lives_ok(format($$select public.set_client_policy(%L, 'metadata_only')$$, (select v from t where k = 'acme')),
  'acme to metadata_only');
select tests.as_postgres();
select is(tests.count_rows('account_assessment', (select v from t where k = 'acme')), 0, 'metadata_only drops assessments');
select is((select count(*)::int from public.person_observation where client_id = (select v from t where k = 'acme')
            and source <> 'manual'), 0, 'metadata_only drops observations learned from bodies');
select is((select count(*)::int from public.person_observation where client_id = (select v from t where k = 'acme')
            and source = 'manual'), 1, 'manual observations stay');
select is((select role_title || ' ' || is_decision_maker::text from public.person where id = (select v from t where k = 'p_acme')),
  'Marketing Manager false', 'the title cache is rebuilt from the remaining observations');
select is(tests.count_rows('signal_event', (select v from t where k = 'acme')), 1, 'metadata signal history stays');
select is(tests.count_rows('outcome', (select v from t where k = 'acme')), 1, 'outcomes stay');

-- Policy: excluded ----------------------------------------------------------------------------------------
select tests.authenticate_as((select a from fx));
select lives_ok(format($$select public.set_client_policy(%L, 'excluded')$$, (select v from t where k = 'globex')),
  'exclude globex');
select tests.as_postgres();
select is(tests.count_rows(tbl, (select v from t where k = 'globex')), 0, format('excluded globex has no %s', tbl))
from p2b where tbl not in ('outcome');
select is(tests.count_rows('outcome', (select v from t where k = 'globex')), 2, 'excluded globex keeps its outcomes (hidden)');
select ok((select not exists (select 1 from jsonb_array_elements(summary -> 'clients') e
                               where e ->> 'client_id' = (select v from t where k = 'globex')::text)
                  and (summary ->> 'stale')::boolean
                  and jsonb_array_length(summary -> 'clients') = 0
             from public.replay_run where id = (select v from t where k = 'run_a')),
  'the replay summary drops the excluded client (acme was dropped at its metadata downgrade) and is marked stale');
select ok(tests.count_rows(tbl, (select v from t where k = 'other')) > 0, format('the other workspace keeps its %s', tbl))
from p2b where tbl <> 'decision_feedback';

select * from finish();
rollback;
