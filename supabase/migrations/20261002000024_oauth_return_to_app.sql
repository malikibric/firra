-- The Mac app starts a mailbox connection and asks the provider flow to return to the app. Besides
-- a site path ("/..."), a return address may now be an app address: firra:// followed by lowercase
-- letters, digits, "/", "_" and "-" only (the API validates the same shape in safeReturnPath).
alter table public.oauth_state drop constraint oauth_state_return_to_check;
alter table public.oauth_state
  add constraint oauth_state_return_to_check
  check (return_to ~ '^/[^/\\]' or return_to ~ '^firra://[a-z0-9/_-]+$');
