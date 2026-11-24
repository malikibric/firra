-- Pending OAuth authorizations (connect flows in apps/api). One-time, short-lived, service role only.

create table public.oauth_state (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  -- sha256 of the state parameter sent to the provider.
  state_hash bytea not null unique,
  provider text not null check (provider in ('google', 'microsoft')),
  -- Exactly one owner: a signed-in member or a contributor using their connect link.
  owner_user_id uuid references auth.users (id) on delete cascade,
  owner_contributor_id uuid,
  -- PKCE verifier (RFC 7636); useless without the authorization code and client secret.
  code_verifier text not null check (char_length(code_verifier) between 43 and 128),
  return_to text not null check (return_to ~ '^/[^/\\]'),
  expires_at timestamptz not null default now() + interval '10 minutes',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, owner_contributor_id) references public.contributor (workspace_id, id)
    on delete cascade,
  check ((owner_user_id is null) <> (owner_contributor_id is null))
);
create index oauth_state_expiry_idx on public.oauth_state (expires_at);

create trigger oauth_state_set_updated_at before update on public.oauth_state
  for each row execute function private.set_updated_at();

alter table public.oauth_state enable row level security;
create policy oauth_state_no_user_access on public.oauth_state
  for select to authenticated using (false);
