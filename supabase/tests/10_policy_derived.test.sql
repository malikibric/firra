-- D15: a client's data policy reaches everything derived from its records and its people.
begin;
\ir _helpers.psql
select no_plan();

create temp table fx on commit drop as
select tests.create_user('owner-a@example.com') as a, tests.create_user('owner-b@example.com') as b;
grant select on fx to authenticated;
create temp table t (k text primary key, v uuid) on commit drop;
grant all on t to authenticated;

\ir _derived_helpers.psql

-- Seeds every derived table for one client through the service role.
create function tests.seed_client(p_ws uuid, p_conn uuid, p_client uuid, p_tag text)
returns uuid
language plpgsql
as $$
declare
  v_rec uuid; v_ev uuid; v_x uuid; v_req uuid; v_need uuid; v_person uuid;
  v_com uuid; v_goal uuid; v_sig uuid; v_card uuid;
begin
  set constraints all deferred;
  insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id,
                                    policy_at_ingest, occurred_at, direction, word_count, body_clean)
  values (p_ws, p_conn, 'email_upload', 'rec-' || p_tag, 'email', p_client, 'full', now(), 'in', 12,
          'We are two weeks late on the report.')
  returning id into v_rec;
  insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
  values (p_ws, v_rec, p_client, 'we are two weeks late', '{"start":3,"end":24}') returning id into v_ev;
  insert into public.extraction (workspace_id, source_record_id, client_id, kind, payload, model, prompt_version,
                                 processing_version, item_index)
  values (p_ws, v_rec, p_client, 'complaint', '{"subtype":"delay"}', 'm', 'extract.v1', 1, 0) returning id into v_x;
  insert into public.extraction_evidence (workspace_id, extraction_id, evidence_id, client_id)
  values (p_ws, v_x, v_ev, p_client);

  v_com := tests.insert_parent('commitment', p_ws, p_client, v_rec, p_tag);
  perform tests.link_evidence('commitment', p_ws, p_client, v_com, v_ev);
  v_goal := tests.insert_parent('client_goal', p_ws, p_client, v_rec, p_tag);
  perform tests.link_evidence('client_goal', p_ws, p_client, v_goal, v_ev);
  v_sig := tests.insert_parent('signal', p_ws, p_client, v_rec, p_tag);
  perform tests.link_evidence('signal', p_ws, p_client, v_sig, v_ev);
  v_card := tests.insert_parent('decision_card', p_ws, p_client, v_rec, p_tag);
  perform tests.link_evidence('decision_card', p_ws, p_client, v_card, v_ev);
  v_req := tests.insert_parent('request', p_ws, p_client, v_rec, p_tag);
  perform tests.link_evidence('request', p_ws, p_client, v_req, v_ev);
  v_need := tests.insert_parent('latent_need', p_ws, p_client, v_rec, p_tag);
  perform tests.link_evidence('latent_need', p_ws, p_client, v_need, v_ev);
  insert into public.latent_need_request (workspace_id, latent_need_id, request_id, client_id)
  values (p_ws, v_need, v_req, p_client);
  insert into public.signal (workspace_id, client_id, type, severity, occurred_at, rule_version, basis, inputs, dedupe_key)
  values (p_ws, p_client, 'reduced_engagement', 'medium', now(), 'v1', 'metadata', '{"messages_30":1}', 'meta-' || p_tag);
  insert into public.client_card (workspace_id, client_id, version, content)
  values (p_ws, p_client, 1, '{"summary":"x"}');
  insert into public.person (workspace_id, client_id, side, display_name)
  values (p_ws, p_client, 'client', 'Person ' || p_tag) returning id into v_person;
  insert into public.person_identity (workspace_id, person_id, kind, value)
  values (p_ws, v_person, 'email', p_tag || '@client.example');
  set constraints all immediate;
  return v_person;
end;
$$;
grant execute on function tests.seed_client(uuid, uuid, uuid, text) to authenticated;

select tests.authenticate_as((select a from fx));
insert into t select 'ws_a', public.create_workspace('A');
insert into t select 'c1', public.create_client((select v from t where k = 'ws_a'), 'One');
insert into t select 'c2', public.create_client((select v from t where k = 'ws_a'), 'Two');
select tests.authenticate_as((select b from fx));
insert into t select 'ws_b', public.create_workspace('B');
insert into t select 'c3', public.create_client((select v from t where k = 'ws_b'), 'Three');

select tests.as_postgres();
with c as (
  insert into public.connection (workspace_id, provider, mode, account_label)
  values ((select v from t where k = 'ws_a'), 'email_upload', 'upload', 'a') returning id)
insert into t select 'conn_a', id from c;
with c as (
  insert into public.connection (workspace_id, provider, mode, account_label)
  values ((select v from t where k = 'ws_b'), 'email_upload', 'upload', 'b') returning id)
insert into t select 'conn_b', id from c;
insert into t select 'p1', tests.seed_client((select v from t where k = 'ws_a'), (select v from t where k = 'conn_a'), (select v from t where k = 'c1'), 'c1');
insert into t select 'p2', tests.seed_client((select v from t where k = 'ws_a'), (select v from t where k = 'conn_a'), (select v from t where k = 'c2'), 'c2');
insert into t select 'p3', tests.seed_client((select v from t where k = 'ws_b'), (select v from t where k = 'conn_b'), (select v from t where k = 'c3'), 'c3');

-- A firm-side person, and client 2's record listing client 1's person among its participants.
with f as (
  insert into public.person (workspace_id, side, display_name)
  values ((select v from t where k = 'ws_a'), 'firm', 'Ana') returning id)
insert into t select 'firm', id from f;
update public.source_record
   set participants = array[(select v from t where k = 'p1'), (select v from t where k = 'p2'), (select v from t where k = 'firm')]
 where external_id = 'rec-c2';
-- Client 2 also has a pending and an errored record.
insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id, policy_at_ingest,
                                  occurred_at, direction, word_count, body_clean, processing_state)
values ((select v from t where k = 'ws_a'), (select v from t where k = 'conn_a'), 'email_upload', 'pending-c2', 'email',
        (select v from t where k = 'c2'), 'full', now(), 'in', 3, 'Please send it soon.', 'pending'),
       ((select v from t where k = 'ws_a'), (select v from t where k = 'conn_a'), 'email_upload', 'error-c2', 'email',
        (select v from t where k = 'c2'), 'full', now(), 'in', 3, 'Another note for you.', 'error');

create temp table derived (tbl text primary key) on commit drop;
insert into derived values ('evidence'), ('extraction'), ('extraction_evidence'), ('commitment'), ('commitment_evidence'),
  ('client_goal'), ('client_goal_evidence'), ('request'), ('request_evidence'), ('latent_need'), ('latent_need_evidence'),
  ('latent_need_request'), ('signal'), ('signal_evidence'), ('decision_card'), ('decision_evidence'), ('client_card');
select ok(tests.count_rows(tbl, (select v from t where k = 'c1')) > 0, format('seeded client 1 has %s', tbl))
from derived;

-- Excluding client 1 ------------------------------------------------------------------------------
select tests.authenticate_as((select a from fx));
select lives_ok(format($$select public.set_client_policy(%L, 'excluded')$$, (select v from t where k = 'c1')), 'exclude client 1');
select tests.as_postgres();
select is(tests.count_rows(tbl, (select v from t where k = 'c1')), 0, format('excluded client 1 has no %s', tbl))
from derived;
select is((select count(*)::int from public.source_record where client_id = (select v from t where k = 'c1')), 0,
  'excluded client 1 has no records');
select is((select count(*)::int from public.person where client_id = (select v from t where k = 'c1')
                or id = (select v from t where k = 'p1')), 0, 'excluded client 1 has no people');
select is((select count(*)::int from public.person_identity where value = 'c1@client.example'), 0,
  'excluded client 1 has no identities');
select is((select count(*)::int from public.person where id = (select v from t where k = 'firm')), 1,
  'firm-side people stay');
select ok(not exists (select 1 from public.source_record r where r.external_id = 'rec-c2'
                       and (select v from t where k = 'p1') = any(r.participants)),
  'records of other clients no longer list the deleted person');
select ok(exists (select 1 from public.source_record r where r.external_id = 'rec-c2'
                   and (select v from t where k = 'p2') = any(r.participants)),
  'the other participants stay');
select ok(tests.count_rows(tbl, (select v from t where k = 'c2')) > 0, format('client 2 keeps its %s', tbl))
from derived;
select ok(tests.count_rows(tbl, (select v from t where k = 'c3')) > 0, format('the other workspace keeps its %s', tbl))
from derived;
select is((select count(*)::int from public.person where client_id = (select v from t where k = 'c2')), 1, 'client 2 keeps its person');
select is((select count(*)::int from public.person where client_id = (select v from t where k = 'c3')), 1, 'other workspace keeps its person');
select ok((select metadata -> 'derived_deleted' ? 'commitment' and metadata -> 'derived_deleted' ? 'person'
             from public.audit_log where action = 'client.policy_changed' and target_id = (select v from t where k = 'c1')),
  'the audit entry carries per-table counts');
select ok((select metadata::text not like '%weeks late%' from public.audit_log where action = 'client.policy_changed'
            and target_id = (select v from t where k = 'c1')), 'the audit entry carries no quote text');

-- Client 2 becomes metadata_only ----------------------------------------------------------------------
select tests.authenticate_as((select a from fx));
select lives_ok(format($$select public.set_client_policy(%L, 'metadata_only')$$, (select v from t where k = 'c2')), 'client 2 to metadata_only');
select tests.as_postgres();
select is(tests.count_rows(tbl, (select v from t where k = 'c2')), 0, format('metadata_only client 2 has no %s', tbl))
from unnest(array['evidence', 'extraction', 'extraction_evidence', 'commitment', 'commitment_evidence', 'client_goal',
                  'client_goal_evidence', 'request', 'request_evidence', 'latent_need', 'latent_need_evidence',
                  'latent_need_request', 'decision_card', 'decision_evidence', 'client_card', 'signal_evidence']) as tbl;
select is((select count(*)::int from public.signal where client_id = (select v from t where k = 'c2') and basis = 'evidence'), 0,
  'evidence-based signals are gone');
select is((select count(*)::int from public.signal where client_id = (select v from t where k = 'c2') and basis = 'metadata'), 1,
  'metadata signals stay');
select is((select count(*)::int from public.person where client_id = (select v from t where k = 'c2')), 1, 'people stay');
select is((select count(*)::int from public.source_record where client_id = (select v from t where k = 'c2')
            and processing_state in ('pending', 'error')), 0, 'nothing is left to process');
select is((select count(*)::int from public.source_record where client_id = (select v from t where k = 'c2')
            and (body_clean is not null or raw_object_uri is not null)), 0, 'bodies are gone');
select ok(tests.count_rows(tbl, (select v from t where k = 'c3')) > 0, format('the other workspace still has %s', tbl))
from derived;

-- The evidence trigger ----------------------------------------------------------------------------
select throws_ok(format($$insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
  values (%L, (select id from public.source_record where external_id = 'rec-c2'), %L, 'we are two weeks late', '{"start":0,"end":1}')$$,
  (select v from t where k = 'ws_a'), (select v from t where k = 'c2')),
  '23514', 'evidence requires a full-policy client', 'no quotes for a metadata_only client');
insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id, policy_at_ingest, occurred_at, direction)
values ((select v from t where k = 'ws_b'), (select v from t where k = 'conn_b'), 'email_upload', 'meta-record', 'email',
        (select v from t where k = 'c3'), 'metadata_only', now(), 'in');
select throws_ok(format($$insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
  values (%L, (select id from public.source_record where external_id = 'meta-record'), %L, 'we are two weeks late', '{"start":0,"end":1}')$$,
  (select v from t where k = 'ws_b'), (select v from t where k = 'c3')),
  '23514', 'evidence requires a full-policy client', 'no quotes on a metadata_only record of a full client');
select lives_ok(format($$insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
  values (%L, (select id from public.source_record where external_id = 'rec-c3'), %L, 'two weeks late on', '{"start":7,"end":24}')$$,
  (select v from t where k = 'ws_b'), (select v from t where k = 'c3')), 'full client, full record: allowed');

select * from finish();
rollback;
