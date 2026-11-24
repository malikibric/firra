-- Local v1 (L2S-02): Slack is a source. The owner's own Slack app and user token are held by the engine (Keychain, memory);
-- here only the shape of what is stored: a connection per Slack workspace (mode `user`, or `upload` for an export import),
-- one connection_scope row per mapped channel (kind `channel`, external_id = channel id) or per client person whose direct
-- messages are read (kind `user`, external_id = Slack user id), source_record.kind = 'chat', person_identity.kind = 'slack_id'.

alter type public.connection_provider add value if not exists 'slack';

-- A channel's display name and what Slack says about it (is_ext_shared, connected team ids, is_private), so the app can
-- suggest mappings and the engine can tell the client's side of a Slack Connect channel from the firm's own.
alter table public.connection_scope
  add column label text check (label is null or char_length(label) <= 200),
  add column meta jsonb not null default '{}'::jsonb check (jsonb_typeof(meta) = 'object' and pg_column_size(meta) <= 2048);
