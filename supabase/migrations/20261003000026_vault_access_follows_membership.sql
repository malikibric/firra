-- Vault delivery follows the current relationship to the workspace (review finding R2-01), and a
-- client that becomes Full again can get its text back (R2-04).
--
-- 1. Removing a member ends the mailboxes they connected in that workspace (status revoked, token
--    wiped) and takes them off as recipient of that workspace's mail, so neither already-synced nor
--    future mail can reach their Mac. Their bodies are then purged by the normal retention rule.
-- 2. Revoking a contributor (revoke_contributor already ends their connections) takes their account
--    off as recipient of the mail of those connections.
-- 3. A client switched back to Full: records stored as metadata only lose their purge mark, so the
--    re-read of its mail (API, on the transition) can store the text again. Records whose body was
--    removed by the retention rule keep their mark (policy_at_ingest stays full for those).

create function private.on_membership_removed()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.connection
     set status = 'revoked', token_ciphertext = null
   where workspace_id = old.workspace_id and owner_user_id = old.user_id and status <> 'revoked';
  update public.source_record
     set vault_user_id = null
   where workspace_id = old.workspace_id and vault_user_id = old.user_id;
  return old;
end;
$$;

create trigger membership_removed_ends_vault
  after delete on public.membership
  for each row execute function private.on_membership_removed();

create function private.on_contributor_revoked()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.source_record r
     set vault_user_id = null
    from public.connection c
   where c.workspace_id = new.workspace_id and c.owner_contributor_id = new.id
     and r.workspace_id = c.workspace_id and r.connection_id = c.id
     and r.vault_user_id = new.user_id;
  return new;
end;
$$;

create trigger contributor_revoked_ends_vault
  after update of status on public.contributor
  for each row
  when (new.status = 'revoked' and old.status is distinct from 'revoked' and new.user_id is not null)
  execute function private.on_contributor_revoked();

create function private.on_client_full_again()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.source_record
     set body_purged_at = null
   where workspace_id = new.workspace_id and client_id = new.id
     and policy_at_ingest = 'metadata_only' and body_clean is null;
  return new;
end;
$$;

create trigger client_full_again_allows_text
  after update of policy on public.client
  for each row
  when (old.policy is distinct from 'full' and new.policy = 'full')
  execute function private.on_client_full_again();

revoke execute on function private.on_membership_removed() from public;
revoke execute on function private.on_contributor_revoked() from public;
revoke execute on function private.on_client_full_again() from public;
