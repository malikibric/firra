-- Records ingested before delivery to the Mac existed have no recipient, so they never reached the
-- owner's vault. The owner of a user-mode connection is the recipient of its mail: a member
-- (owner_user_id) or an active contributor with an account. Org-wide and upload connections have no
-- single owner and keep no recipient.
update public.source_record r
   set vault_user_id = coalesce(
         c.owner_user_id,
         (select ct.user_id from public.contributor ct
           where ct.workspace_id = c.workspace_id and ct.id = c.owner_contributor_id
             and ct.status = 'active'))
  from public.connection c
 where r.connection_id = c.id
   and r.workspace_id = c.workspace_id
   and c.mode = 'user'
   and r.vault_user_id is null;
