# Test Slack workspace and E2E (L2S-14)

Fills a throwaway Slack workspace with the four Northbeam Slack scenarios from
`docs/plans/local/multichannel-signals.md` §8, then runs the real Slack flow
end to end: connect → map → sync → process → evidence with a Slack permalink
in an MCP answer. Script: `scripts/seed-test-slack/seed_test_slack.py`
(dry-run unless `--live`; tokens only from env vars, never committed).
Synthetic content only — never invite a real client workspace.

## 0. One-time setup (owner)

1. Create a free test workspace (e.g. **Firra Test Northbeam**) from a test
   account. Record whether a free workspace allows the steps below (plan L2S-14
   open point: free-workspace rights unverified — app creation, channel
   creation and posting as a second member).
2. In the test workspace, create a **separate throwaway seeder app** (not the
   Firra manifest — Firra's app is read-only by design,
   `services/worker/firra/connectors/slack/auth.py`). Minimal manifest:
   user scopes `channels:read`, `channels:manage`, `channels:history`,
   `chat:write`, `users:read`. Install it **to the test workspace only** and
   copy the **user token** (`xoxp-`):
   `export FIRRA_TEST_SLACK_TOKEN=...` (this shell only).
3. Optional, for true side attribution: a second free test workspace connected
   through Slack Connect to the seed channels, with its own seeder install —
   `export FIRRA_TEST_SLACK_CLIENT_TOKEN=...` (posts the client-side lines).
   A second member in the test workspace (the account manager) —
   `export FIRRA_TEST_SLACK_AM_TOKEN=...` (posts the firm-side AM lines of
   scenario (d)). Without these, those lines post as the owner and the dry-run
   report says which signal expectations cannot fire.
4. The script refuses any workspace except `--workspace`, and `auth.test`
   verifies each token before posting.

## 1. Seed

```bash
# Plan only, no network writes:
uv run scripts/seed-test-slack/seed_test_slack.py --workspace "Firra Test Northbeam"
# Expected: channels=4 messages=36 (alder=8, birch=10, cedar=5, dogwood=13),
# plus warnings for every missing optional token.

# Post (idempotent; re-runs skip posted keys via state file):
uv run scripts/seed-test-slack/seed_test_slack.py --workspace "Firra Test Northbeam" --live

# Compare the workspace with the plan afterwards (or alone):
uv run scripts/seed-test-slack/seed_test_slack.py --workspace "Firra Test Northbeam" --live --verify-only
```

State lives in `~/.cache/firra-seed-test-slack-state.json` (channel ids,
message `ts`, permalinks). `--reset-state` starts over; the script reuses
existing channels by name.

What lands: `firra-seed-alder` (a, benign control, routine and positive),
`firra-seed-birch` (b, three complaints — delay, quality, communication —
plus renewal pressure), `firra-seed-cedar` (c, a firm commitment dated 7 days
before the seed run, then a client nudge), `firra-seed-dogwood` (d, mostly
the account manager, one owner message). Slack cannot backdate posts, so all
messages carry today's date; scenario timing that matters is written as
explicit dates in the text, which is what extraction reads.

## 2. E2E in Firra

1. Connect Slack in Firra with the **owner's Firra token** (read scopes only):
   `POST /local/connections/slack` (connect), `GET /local/connections/slack/{id}/channels` (list),
   `PUT /local/connections/slack/{id}/scope` (map the four seed channels to four test
   clients: Alder, Birchfield, Cedarline, Dogwood). The Mac Settings screen
   for this lands with L2S-13.
2. Sync, then process (owner's AI or in-conversation — recorded/fake
   executors in dev, real run only with the owner's go on their subscription).
3. Checks:
   - every seeded message syncs (`source_record.kind = chat` with
     `metadata.channel_id`);
   - extraction items verify (quotes match the cleaned Slack text);
   - evidence rows carry Slack permalinks (`chat.getPermalink` values are in
     the state file — compare one per channel);
   - an MCP answer about a seeded client (e.g. `search_evidence`,
     `get_risks`) contains a Slack link with `source: slack`.
4. Scenario expectations (full firing needs the optional tokens from §0.3):
   (a) stays below `elevated`; (b) raises an evidence-backed complaint signal;
   (c) raises `late_commitment` (the due date is set 7 days before the seed run); (d) suppresses
   volume rules with the `participant_coverage` caveat.

## 3. Wipe and start again

Archive or delete the four seed channels in the test workspace, revoke or
delete the seeder app (its tokens die with it), and remove the state file:

```bash
rm ~/.cache/firra-seed-test-slack-state.json
```
