-- Connections to providers (mailboxes, calendars, uploads) and the per-workspace data key.

-- Per-workspace data-encryption key, wrapped by Cloud KMS (token-kek). Service role only.
create table public.workspace_key (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null unique references public.workspace (id) on delete cascade,
  wrapped_dek bytea not null,
  kek_name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger workspace_key_set_updated_at before update on public.workspace_key
  for each row execute function private.set_updated_at();

alter table public.workspace_key enable row level security;
-- No user may ever read key material; the policy documents that explicitly.
create policy workspace_key_no_user_access on public.workspace_key
  for select to authenticated using (false);

create type public.connection_provider as enum ('gmail', 'google_calendar', 'outlook', 'outlook_calendar', 'email_upload');
create type public.connection_mode as enum ('user', 'org_delegated', 'org_app', 'upload');
create type public.connection_status as enum ('active', 'error', 'revoked');
create type public.scope_kind as enum ('user', 'channel', 'project', 'calendar');

create table public.connection (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  provider public.connection_provider not null,
  mode public.connection_mode not null,
  owner_contributor_id uuid,
  owner_user_id uuid references auth.users (id) on delete set null,
  -- Mailbox address or tenant/domain for org modes; shown in the UI.
  account_label text not null check (char_length(account_label) between 1 and 320),
  -- Provider's stable account id (Google sub, Microsoft oid, tenant id); null for uploads.
  external_account_id text,
  status public.connection_status not null default 'active',
  scopes text[] not null default '{}',
  -- Envelope-encrypted OAuth tokens (see services/worker/firra/crypto). Never readable by users.
  token_ciphertext text,
  cursor jsonb not null default '{}'::jsonb check (jsonb_typeof(cursor) = 'object'),
  backfill_months smallint not null default 12 check (backfill_months between 0 and 24),
  last_synced_at timestamptz,
  last_error_class text check (last_error_class ~ '^[a-z][a-z0-9_]*$'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, owner_contributor_id) references public.contributor (workspace_id, id)
    on delete cascade,
  unique (workspace_id, id),
  check (mode = 'upload' or external_account_id is not null)
);
create unique index connection_account_unique on public.connection (workspace_id, provider, mode, external_account_id)
  where external_account_id is not null;
create index connection_owner_contributor_idx on public.connection (workspace_id, owner_contributor_id);

create table public.connection_scope (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  connection_id uuid not null,
  kind public.scope_kind not null,
  external_id text not null check (char_length(external_id) between 1 and 320),
  client_id uuid,
  include boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, connection_id) references public.connection (workspace_id, id) on delete cascade,
  foreign key (workspace_id, client_id) references public.client (workspace_id, id) on delete set null (client_id),
  unique (connection_id, kind, external_id)
);

alter table public.job
  add constraint job_connection_fk foreign key (workspace_id, connection_id)
  references public.connection (workspace_id, id) on delete set null (connection_id);

create trigger connection_set_updated_at before update on public.connection
  for each row execute function private.set_updated_at();
create trigger connection_scope_set_updated_at before update on public.connection_scope
  for each row execute function private.set_updated_at();

alter table public.connection enable row level security;
alter table public.connection_scope enable row level security;

create policy connection_select_member on public.connection
  for select to authenticated using (private.is_member(workspace_id));
create policy connection_scope_select_member on public.connection_scope
  for select to authenticated using (private.is_member(workspace_id));

grant select (id, workspace_id, provider, mode, owner_contributor_id, owner_user_id, account_label,
              status, scopes, backfill_months, last_synced_at, last_error_class, created_at, updated_at)
  on public.connection to authenticated;
grant select on public.connection_scope to authenticated;

-- Revoking a contributor also revokes their connections and wipes stored tokens.
create function public.revoke_contributor(p_contributor_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_workspace_id uuid;
begin
  perform private.require_uid();
  select workspace_id into v_workspace_id from public.contributor where id = p_contributor_id;
  if v_workspace_id is null
     or not private.has_role(v_workspace_id, array['owner', 'admin']::public.member_role[]) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  update public.contributor set status = 'revoked', token_hash = null where id = p_contributor_id;
  update public.connection
     set status = 'revoked', token_ciphertext = null
   where workspace_id = v_workspace_id and owner_contributor_id = p_contributor_id;

  perform private.write_audit(v_workspace_id, 'contributor.revoked', 'contributor', p_contributor_id);
end;
$$;

grant execute on function public.revoke_contributor(uuid) to authenticated;
