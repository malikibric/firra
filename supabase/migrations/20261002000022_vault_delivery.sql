-- Mail bodies live on the owner's Mac (docs/plans/d-mac-app.md D-D9). The server keeps a body only
-- until it is processed and delivered to that Mac (72 hours at most undelivered, 7 days at most
-- overall), and no signed-in user can read bodies through the database any more: the vault endpoint
-- of the API (service role, the caller's own mailboxes only) is the only way out.

alter table public.source_record
  -- Whose Mac receives this record: the user who owns the connection (a member, or a contributor
  -- with an account). Null for uploads and org-wide modes: nobody to deliver to.
  add column vault_user_id uuid references auth.users (id) on delete set null,
  add column vault_delivered_at timestamptz;

create index source_record_vault_pending_idx
  on public.source_record (vault_user_id, created_at, id)
  where vault_user_id is not null and vault_delivered_at is null and body_clean is not null;

-- Replace the table-wide select with a column list that leaves out body_clean and raw_object_uri.
revoke select on public.source_record from authenticated;
grant select (id, workspace_id, connection_id, provider, external_id, kind, client_id,
              policy_at_ingest, thread_key, occurred_at, author_person_id, direction, participants,
              word_count, metadata, body_purged_at, deep_link, content_hash, created_at, updated_at,
              processing_state, processed_at, processing_version, vault_delivered_at)
  on public.source_record to authenticated;
