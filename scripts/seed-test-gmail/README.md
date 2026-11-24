# Seed a test Gmail + Calendar account (L2-04)

Fills a **test Google account** with the Northbeam corpus (700 messages in 306
threads, 2025-10-01 → 2026-09-30; 346 calendar events) with **original dates**,
so the whole real Gmail flow (sync → extraction → signals) can be tested
without real client mail. No real run happens in this package; the lead and
the owner run it later (owner task, plan day 8).

## One-time Google Cloud setup (owner)

1. Open [Google Cloud Console](https://console.cloud.google.com/) → create a
   project (e.g. `firra-test-seed`) → **APIs & Services → Library** → enable
   **Gmail API** and **Google Calendar API**.
2. **APIs & Services → OAuth consent screen** → User type **External** →
   **Publishing status: Testing** → add the test account
   (e.g. `firra.test.northbeam@gmail.com`) under **Test users**.
   - Testing mode limits (research S1): max **100 test users**; refresh
     tokens **expire after 7 days** (all client types except basic-identity
     scopes). Re-run OAuth weekly during the test period.
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   → Application type **Desktop app** → note the client ID and secret.
4. Export them only in the shell that runs the seed (never commit):
   `export GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=...`

## Run

```bash
# What would be built (no network):
uv run scripts/seed-test-gmail/seed_test_gmail.py --account firra.test.northbeam@gmail.com --dry-run
# Expected: messages=700 threads=306 inbound=365 outbound=335 events=346
# earliest=2025-10-01T17:22:00Z latest=2026-09-30T20:29:00Z

# Seed (browser opens once for the Desktop OAuth consent; token stays in memory):
uv run scripts/seed-test-gmail/seed_test_gmail.py --account firra.test.northbeam@gmail.com

# Compare the account with the corpus afterwards (or alone):
uv run scripts/seed-test-gmail/seed_test_gmail.py --account firra.test.northbeam@gmail.com --verify-only
```

Behaviour: firm-side addresses (`@northbeam.example`) are rewritten to the
test account; client addresses on fictional domains are kept; threads are
re-chained from `thread_key`; outbound mail is labelled `SENT`, inbound
`INBOX`. Import uses `messages.import?internalDateSource=dateHeader` so the
original `Date` header becomes the Gmail internal date. A re-run imports
nothing (skips by `rfc822msgid:` search + local state in
`~/.cache/firra-seed-test-gmail-state.json`). The script refuses to run
unless the signed-in address equals `--account` (or `--allow-domain`), so a
run against a real mailbox is impossible. Rate limits: 0.5 s pacing +
`Retry-After`/backoff on 429 and Gmail throttling 403s.

If `import` ever answers `403 insufficientPermissions` for `gmail.insert`,
rerun with `--use-fallback-scopes` (`gmail.modify`). If neither `import` nor
`insert` can keep original dates, the fallback is IMAP `APPEND` with
`INTERNALDATE` (risk R6; needs an app password on the test account and is
documented here so the run is not blocked silently).

## Wipe and start again

Delete the seeded mail with a Gmail search the owner runs themselves, e.g.
search `newer_than` the seed window or delete by label after moving seeded
mail there; delete seeded calendar events by the `firraSeedL204` extended
property (list → delete). Then remove the state file:

```bash
rm ~/.cache/firra-seed-test-gmail-state.json
```
