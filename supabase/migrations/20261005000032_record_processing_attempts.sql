-- Local v1 (L3-01): the owner's AI may return no answer for a record or malformed output for a packet. Each such
-- outcome counts one attempt on the record; the processor parks a record (processing_state 'error') after 3, so a
-- record can never loop forever across jobs or restarts. Reset to 0 by the ingest upsert when the content changes.
alter table public.source_record
  add column processing_attempts smallint not null default 0
  check (processing_attempts between 0 and 20);
