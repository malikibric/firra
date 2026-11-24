-- P2 schema part 2: derived memory, signals, client card and decision cards.
-- Isolation between workspaces, hidden excluded clients, evidence or nothing, client-consistent links.
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

-- Fixtures written by the worker (service role): records and verified evidence --------------------
select tests.as_postgres();
with c as (
  insert into public.connection (workspace_id, provider, mode, account_label)
  values ((select v from t where k = 'ws_a'), 'email_upload', 'upload', 'export.mbox') returning id)
insert into t select 'conn', id from c;
with r as (
  insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id, policy_at_ingest,
                                    occurred_at, direction, word_count, body_clean)
  values ((select v from t where k = 'ws_a'), (select v from t where k = 'conn'), 'email_upload', 'm-1', 'email',
          (select v from t where k = 'acme'), 'full', now(), 'in', 12, 'We are two weeks late on the report.'),
         ((select v from t where k = 'ws_a'), (select v from t where k = 'conn'), 'email_upload', 'm-2', 'email',
          (select v from t where k = 'globex'), 'full', now(), 'in', 12, 'We are two weeks late on the audit.')
  returning id, client_id)
insert into t select case when client_id = (select v from t where k = 'acme') then 'rec_acme' else 'rec_globex' end, id from r;
with e as (
  insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
  values ((select v from t where k = 'ws_a'), (select v from t where k = 'rec_acme'), (select v from t where k = 'acme'),
          'we are two weeks late', '{"start":3,"end":24}'),
         ((select v from t where k = 'ws_a'), (select v from t where k = 'rec_globex'), (select v from t where k = 'globex'),
          'we are two weeks late', '{"start":3,"end":24}')
  returning id, client_id)
insert into t select case when client_id = (select v from t where k = 'acme') then 'ev_acme' else 'ev_globex' end, id from e;

-- People: champion, departure and reversible merges -------------------------------------------------
with p as (
  insert into public.person (workspace_id, client_id, side, display_name)
  values ((select v from t where k = 'ws_a'), (select v from t where k = 'acme'), 'client', 'Jane'),
         ((select v from t where k = 'ws_b'), null, 'client', 'Jane')
  returning id, workspace_id)
insert into t select case when workspace_id = (select v from t where k = 'ws_a') then 'jane_a' else 'jane_b' end, id from p;
select is((select is_champion from public.person where id = (select v from t where k = 'jane_a')), false,
  'people are not champions by default');
select lives_ok(format($$update public.person set is_champion = true, departed_at = now() where id = %L$$,
  (select v from t where k = 'jane_a')), 'champion and departure can be recorded');
select throws_ok(format($$update public.person set merged_into_person_id = id where id = %L$$,
  (select v from t where k = 'jane_a')), '23514', null, 'a person cannot be merged into itself');
select throws_ok(format($$update public.person set merged_into_person_id = %L where id = %L$$,
  (select v from t where k = 'jane_b'), (select v from t where k = 'jane_a')),
  '23503', null, 'a merge cannot point at another workspace');

-- Evidence or nothing: every derived table --------------------------------------------------------
select lives_ok(format($sql$
  do $do$
  declare p uuid;
  begin
    set constraints all deferred;
    p := tests.insert_parent(%1$L, %2$L, %3$L, %4$L, 'acme');
    perform tests.link_evidence(%1$L, %2$L, %3$L, p, %5$L);
    p := tests.insert_parent(%1$L, %2$L, %6$L, %7$L, 'globex');
    perform tests.link_evidence(%1$L, %2$L, %6$L, p, %8$L);
    set constraints all immediate;
  end $do$ $sql$, tbl, (select v from t where k = 'ws_a'),
  (select v from t where k = 'acme'), (select v from t where k = 'rec_acme'), (select v from t where k = 'ev_acme'),
  (select v from t where k = 'globex'), (select v from t where k = 'rec_globex'), (select v from t where k = 'ev_globex')),
  format('%s with evidence commits', tbl))
from unnest(array['commitment', 'client_goal', 'request', 'latent_need', 'signal', 'decision_card']) as tbl;

select throws_ok(format($sql$
  do $do$
  begin
    set constraints all deferred;
    perform tests.insert_parent(%1$L, %2$L, %3$L, %4$L, 'bare');
    set constraints all immediate;
  end $do$ $sql$, tbl, (select v from t where k = 'ws_a'),
  (select v from t where k = 'acme'), (select v from t where k = 'rec_acme')),
  '23514', tbl || ' requires evidence', format('%s without evidence is rejected', tbl))
from unnest(array['commitment', 'client_goal', 'request', 'latent_need', 'signal', 'decision_card']) as tbl;

select throws_ok(format($sql$
  do $do$
  declare p uuid;
  begin
    set constraints all deferred;
    p := tests.insert_parent(%1$L, %2$L, %3$L, %4$L, 'crossed');
    perform tests.link_evidence(%1$L, %2$L, %3$L, p, %5$L);
    set constraints all immediate;
  end $do$ $sql$, tbl, (select v from t where k = 'ws_a'),
  (select v from t where k = 'acme'), (select v from t where k = 'rec_acme'), (select v from t where k = 'ev_globex')),
  '23503', null, format('%s cannot cite another client''s evidence', tbl))
from unnest(array['commitment', 'client_goal', 'request', 'latent_need', 'signal', 'decision_card']) as tbl;

-- Tenancy: a row cannot point at another workspace's client -----------------------------------------
select throws_ok(format($$insert into public.commitment (workspace_id, client_id, owner_side, text, stated_at, dedupe_key)
  values (%L, %L, 'firm', 'x', now(), 'foreign')$$, (select v from t where k = 'ws_a'), (select v from t where k = 'other')),
  '23503', null, 'derived rows cannot reference another workspace''s client');

-- Signals ---------------------------------------------------------------------------------------------
select throws_ok(format($$insert into public.signal (workspace_id, client_id, type, severity, occurred_at, rule_version, basis, dedupe_key)
  values (%L, %L, 'reduced_engagement', 'medium', now(), 'v1', 'metadata', 'no-inputs')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', null, 'a metadata signal must store its inputs');
select throws_ok(format($$insert into public.signal (workspace_id, client_id, type, severity, occurred_at, rule_version, basis, inputs, dedupe_key)
  values (%L, %L, 'tone_decline', 'high', now(), 'v1', 'metadata', '{"share":0.6}', 'tone')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', null, 'tone_decline cannot rest on metadata alone');
select throws_ok(format($$insert into public.signal (workspace_id, client_id, type, severity, occurred_at, rule_version, basis, inputs, dedupe_key)
  values (%L, %L, 'Bad Type', 'low', now(), 'v1', 'metadata', '{}', 'bad')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', null, 'signal types are snake_case identifiers');
select lives_ok(format($sql$
  do $do$
  begin
    set constraints all deferred;
    insert into public.signal (workspace_id, client_id, type, severity, occurred_at, rule_version, basis, inputs, dedupe_key)
    values (%L, %L, 'reduced_engagement', 'medium', now(), 'v1', 'metadata', '{"messages_30d":2,"baseline_30d":11}', '2026-09');
    set constraints all immediate;
  end $do$ $sql$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  'a metadata signal commits without evidence');
select throws_ok(format($$insert into public.signal (workspace_id, client_id, type, severity, occurred_at, rule_version, basis, inputs, dedupe_key)
  values (%L, %L, 'reduced_engagement', 'medium', now(), 'v2', 'metadata', '{}', '2026-09')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23505', null, 'a signal is unique per client, type and dedupe key');

-- Requests, latent needs -----------------------------------------------------------------------------
select throws_ok(format($$insert into public.request (workspace_id, client_id, origin_kind, text, source_record_id, occurred_at, embedding, dedupe_key)
  values (%L, %L, 'request', 'x', %L, now(), (select array_fill(0.1::real, array[768])), 'emb')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme'), (select v from t where k = 'rec_acme')),
  '23514', null, 'an embedding must name its model');
select throws_ok(format($$insert into public.request (workspace_id, client_id, origin_kind, text, source_record_id, occurred_at, dedupe_key)
  values (%L, %L, 'praise', 'x', %L, now(), 'kind')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme'), (select v from t where k = 'rec_acme')),
  '23514', null, 'only requests and complaints become request rows');
select throws_ok(format($$insert into public.request (workspace_id, client_id, origin_kind, text, source_record_id, occurred_at, dedupe_key)
  values (%L, %L, 'request', 'x', %L, now(), 'wrong-client')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'globex'), (select v from t where k = 'rec_acme')),
  '23503', null, 'a request carries the client of its record');
select throws_ok(format($$insert into public.latent_need (workspace_id, client_id, mention_count, thread_count, first_seen_at, last_seen_at, cluster_key, rule_version)
  values (%L, %L, 2, 2, now(), now(), 'few', 'v1')$$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', null, 'a latent need needs at least 3 mentions');
select throws_ok(format($$insert into public.latent_need (workspace_id, client_id, mention_count, thread_count, first_seen_at, last_seen_at, cluster_key, rule_version)
  values (%L, %L, 3, 1, now(), now(), 'one-thread', 'v1')$$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', null, 'a latent need needs at least 2 threads');

select lives_ok(format($sql$
  do $do$
  declare r uuid; n uuid;
  begin
    set constraints all deferred;
    r := tests.insert_parent('request', %1$L, %2$L, %3$L, 'lnr');
    perform tests.link_evidence('request', %1$L, %2$L, r, %4$L);
    n := tests.insert_parent('latent_need', %1$L, %2$L, %3$L, 'lnr');
    perform tests.link_evidence('latent_need', %1$L, %2$L, n, %4$L);
    insert into public.latent_need_request (workspace_id, latent_need_id, request_id, client_id) values (%1$L, n, r, %2$L);
    set constraints all immediate;
  end $do$ $sql$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme'),
  (select v from t where k = 'rec_acme'), (select v from t where k = 'ev_acme')),
  'a latent need links the requests behind its counts');
select throws_ok(format($sql$
  do $do$
  declare r uuid; n uuid;
  begin
    set constraints all deferred;
    r := tests.insert_parent('request', %1$L, %2$L, %3$L, 'lnr-globex');
    perform tests.link_evidence('request', %1$L, %2$L, r, %4$L);
    n := tests.insert_parent('latent_need', %1$L, %5$L, %6$L, 'lnr-cross');
    perform tests.link_evidence('latent_need', %1$L, %5$L, n, %7$L);
    insert into public.latent_need_request (workspace_id, latent_need_id, request_id, client_id) values (%1$L, n, r, %5$L);
    set constraints all immediate;
  end $do$ $sql$, (select v from t where k = 'ws_a'), (select v from t where k = 'globex'),
  (select v from t where k = 'rec_globex'), (select v from t where k = 'ev_globex'),
  (select v from t where k = 'acme'), (select v from t where k = 'rec_acme'), (select v from t where k = 'ev_acme')),
  '23503', null, 'a latent need cannot count another client''s requests');

-- Client card, decision cards -------------------------------------------------------------------------
select lives_ok(format($$insert into public.client_card (workspace_id, client_id, version, content)
  values (%L, %L, 1, '{"summary":"x"}')$$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  'client card version 1 is stored');
select throws_ok(format($$insert into public.client_card (workspace_id, client_id, version, content)
  values (%L, %L, 1, '{}')$$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23505', null, 'client card versions are unique per client');
select throws_ok(format($$insert into public.client_card (workspace_id, client_id, version, content)
  values (%L, %L, 2, '[]')$$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', null, 'client card content must be an object');
select throws_ok(format($$insert into public.decision_card (workspace_id, client_id, week, kind, title, body, locale, score, priority,
    inputs, model, prompt_version, dedupe_key, play, facts, interpretation, recommendation, strength)
  values (%L, %L, '2026-09-30', 'risk', 't', 'b', 'en', 1, 1, '{}', 'm', 'cards.v1', 'wednesday', 'fix_first', '[]', 'i', 'r', 'weak')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', null, 'a decision card week starts on a Monday');
select throws_ok(format($$insert into public.decision_card (workspace_id, client_id, week, kind, title, body, locale, score, priority,
    inputs, model, prompt_version, dedupe_key, play, facts, interpretation, recommendation, strength)
  values (%L, %L, '2026-09-28', 'risk', 't', 'b', 'english', 1, 1, '{}', 'm', 'cards.v1', 'locale', 'fix_first', '[]', 'i', 'r', 'weak')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '23514', null, 'a decision card locale is a language code');

-- Read path ---------------------------------------------------------------------------------------------
select tests.authenticate_as((select a from fx));
select ok(tests.count_rows(tbl, (select v from t where k = 'acme')) > 0, format('member sees acme %s', tbl))
from unnest(array['commitment', 'client_goal', 'request', 'latent_need', 'signal', 'decision_card', 'client_card',
                  'commitment_evidence', 'client_goal_evidence', 'request_evidence', 'latent_need_evidence',
                  'signal_evidence', 'decision_evidence', 'latent_need_request']) as tbl;
select throws_ok(format($$insert into public.commitment (workspace_id, client_id, owner_side, text, stated_at, dedupe_key)
  values (%L, %L, 'firm', 'x', now(), 'user')$$, (select v from t where k = 'ws_a'), (select v from t where k = 'acme')),
  '42501', null, 'users cannot write commitments');
select throws_ok($$delete from public.signal$$, '42501', null, 'users cannot delete signals');

select tests.authenticate_as((select b from fx));
select is(tests.count_rows(tbl), 0, format('B cannot see A %s', tbl))
from unnest(array['commitment', 'client_goal', 'request', 'latent_need', 'signal', 'decision_card', 'client_card',
                  'commitment_evidence', 'client_goal_evidence', 'request_evidence', 'latent_need_evidence',
                  'signal_evidence', 'decision_evidence', 'latent_need_request']) as tbl;

-- Excluding a client hides whatever derived rows survive.
select tests.authenticate_as((select a from fx));
select lives_ok(format($$select public.set_client_policy(%L, 'excluded')$$, (select v from t where k = 'acme')), 'exclude acme');
select is(tests.count_rows(tbl, (select v from t where k = 'acme')), 0, format('excluded client %s is hidden', tbl))
from unnest(array['commitment', 'client_goal', 'request', 'latent_need', 'signal', 'decision_card', 'client_card',
                  'commitment_evidence', 'client_goal_evidence', 'request_evidence', 'latent_need_evidence',
                  'signal_evidence', 'decision_evidence', 'latent_need_request']) as tbl;
select ok(tests.count_rows(tbl, (select v from t where k = 'globex')) > 0, format('other clients keep their %s', tbl))
from unnest(array['commitment', 'client_goal', 'request', 'latent_need', 'signal', 'decision_card',
                  'commitment_evidence', 'client_goal_evidence', 'request_evidence', 'latent_need_evidence',
                  'signal_evidence', 'decision_evidence']) as tbl;

select * from finish();
rollback;
