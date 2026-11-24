-- "Fetch my mail again": when a Mac lost its vault, the server fetches the owner's messages from
-- the provider again, puts the bodies back for delivery and the retention clock restarts from here.
-- Extraction state is untouched: nothing is analysed or paid for twice.

alter table public.source_record
  add column rehydrated_at timestamptz;

comment on column public.source_record.rehydrated_at is
  'Set when the body was fetched again from the provider for delivery to the owner''s Mac. Retention counts from the later of created_at and rehydrated_at.';
