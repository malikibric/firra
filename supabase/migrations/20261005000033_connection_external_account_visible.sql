-- The account identity of a connection (the mailbox address or the Slack user id) is not a secret;
-- the signal facts loader reads it, also when it runs as the signed-in owner (MCP meeting
-- preparation), to know which firm-side people are the owner (participant coverage, signals.v3).
grant select (external_account_id) on public.connection to authenticated;
