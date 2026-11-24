# E2E protocol (L10-01): manual pass over plan §1 on the test accounts

Proves every row of `docs/plans/local-v1.md` §1 (items 1–13) on a Mac with the
DMG, the test Gmail, the test Slack workspace and Claude Code. Synthetic data
only — never a real client workspace. Every route, flag and command below was
verified against `main` (see "Verified against"); anything else is marked
**UNVERIFIED** with the reason, and steps needing unmerged work are marked
**BLOCKED: \<WP\>**.

## Rig

- Apple Silicon Mac, macOS 14+. DMG from `apps/mac/scripts/build-app.sh [version]`
  (builds Swift release + bundled engine + `Firra-$VERSION.dmg`, ad-hoc signed).
- Test Gmail (e.g. `firra.test.northbeam@gmail.com`), seeded with
  `scripts/seed-test-gmail/seed_test_gmail.py` (700 messages / 306 threads /
  346 events; `--dry-run`, `--account`, `--verify-only`).
- Test Slack workspace (e.g. "Firra Test Northbeam"), seeded with
  `scripts/seed-test-slack/seed_test_slack.py` (36 messages / 4 channels;
  dry-run by default, `--live`, `--verify-only`; `FIRRA_TEST_SLACK_TOKEN`).
  `docs/runbooks/test-slack.md` has the setup.
- Claude Code signed in (for background extraction + MCP stdio). A second Mac
  account for item 10.
- Conventions: each item ends with `Pass:` — fill `pass/fail + initials + date`.
  Client names below are placeholders; substitute the seeded names. Engine API
  base is `http://127.0.0.1:<port>` with the per-start bearer token (the app
  shows it; `GET /local/status` answers `EngineStatus` with `connections[]`
  and `ai.queue{pending, done, rejected_items}`).

## 1. Install

Preconditions: clean macOS user (no `~/Library/Application Support/Firra`).
Steps: follow `docs/runbooks/clean-install.md` §§0–2 (hash check, drag to
Applications, right-click Open, Open Anyway).
Expected: app opens, menu-bar icon appears, nothing starts before onboarding.
Pass: __

## 2. Onboarding

Preconditions: item 1 done, test Google account ready.
Steps (app, L5-03 screens): welcome → privacy screen (mail stays on the Mac,
owner's AI only) → Connect Google (system browser, Desktop OAuth, test
account, scopes `gmail.readonly`, `calendar.readonly`, `openid email`) →
backfill depth 3 months → clients typed by the owner (name + email domain; mail is
read only for these domains, after they are saved; the optional header suggestions
`POST /local/onboarding/suggestions {"connection_id"} → 202 running`,
`GET /local/onboarding/suggestions` → `ready` with items are unticked until chosen) →
2–3 clients, set one to `metadata_only` → AI processing step (Claude Code
installed → background offered; without it → "only in conversation").
Expected: connection shows the test address and "syncing"; suggestion list
appears; the `metadata_only` client later shows counts but no quotes.
Pass: __

## 3. Mail and calendar sync, nothing lost

Preconditions: item 2 done.
Steps:
1. `uv run scripts/seed-test-gmail/seed_test_gmail.py --account <test> --dry-run`
   → note `messages=700 threads=306 events=346`; then without `--dry-run` to seed.
2. Connect the mailbox in the app; backfill runs (one `sync.backfill_chunk`
   per month; trigger now with `POST /local/connections/{id}/sync → 202 queued`).
3. Re-run the seed script with `--verify-only`; compare
   `GET /local/status` → `connections[]` (`status: active`, `last_synced_at`
   fresh) and `POST /local/diagnostics/export` → record/message counts.
Expected: verify-only imports nothing new (idempotent); status shows active
connections; diagnostics counts match the corpus (± threads the owner deleted).
Pass: __

## 3b. Slack connect and evidence

Preconditions: test workspace seeded (`--live`), Firra connected to nothing yet.
Steps:
1. Create the owner's Slack app from the Firra manifest link, install to the
   **test workspace only**, paste the user token in Firra
   (`POST /local/connections/slack {"connection_id","account_label"}`).
2. `GET /local/connections/slack/{id}/channels` → the four `firra-seed-*`
   channels; `PUT /local/connections/slack/{id}/scope` mapping each to a test
   client (Alder, Birchfield, Cedarline, Dogwood).
3. Sync; ask via MCP `search_evidence` for a seeded complaint and open one
   evidence item (`GET /evidence/{id}`).
Expected:permalink evidence with `source: slack`; a client present in both
mail and Slack shows continuity (no `reduced_engagement` from the move —
scenario (a) stays below `elevated`).
Pass: __

## 4. Extraction through the owner's AI

Preconditions: items 3–3b synced, records `pending`
(`GET /local/status` → `ai.queue.pending > 0`).
Steps:
1. Background: `PUT /local/ai/settings`
   `{"executor":"claude_code","backfill_months":3,"background_acknowledged":true}`
   (executor ∈ `claude_code|codex|conversation_only`; acknowledgement required
   or 422 `background_acknowledgement_required`). `GET /local/ai/status`,
   `POST /local/ai/probe` to check the tool answers.
2. Wait for drain (`ai.queue.pending → 0`); or in-conversation: MCP
   `get_pending_mail` (lease ≤ 10 records, default 5; needs `mcp_processing_tools` on in
   `settings.json`) → answer → `submit_extractions`.
Expected: extractions verify (quotes verbatim in cleaned sources; rejections
counted in `rejected_items`); Today shows states with drivers and evidence.
Pass: __

## 5. Screens and notification budget

Preconditions: item 4 done.
Steps: open Today (= AttentionView), What changed, Clients, Client detail
(state, drivers with evidence, stakeholders + champion, commitments, play),
Replay history, Routines (flip morning scan off/on via
`PUT /v1/workspaces/{id}/routines/{kind} {"enabled":false}` → persists across
restart), Decisions.
Expected: every claim has an evidence link; `excluded` absent;
`metadata_only` shows counts, never quotes. Notifications: permission on, only
*important* notify (budget ≤ 3/week enforced by the routine engine — covered
by the T49e eval; manual pass just confirms no spam during the run).
Limitation: no Mail screen on `main` (verify mail bodies via
`search_evidence`/`get_evidence` instead); no routine time-shift (no trigger
route — enable and check the next scheduled run).
Pass: __

## 6. Local MCP (stdio)

Preconditions: item 4 done.
Steps: Connect AI tools screen → `GET /local/mcp/connect` returns one item per
client; run the `claude_code` command (`claude mcp add firra -- ...`,
server `python -I -B -m firra.local.mcp.stdio`). In Claude Code ask "Which
clients need my attention?" (calls `get_clients_needing_attention`).
Expected: answer from Firra data with evidence ids; Cursor/VS Code deeplinks
and the Desktop config from the same screen also resolve.
Pass: __

## 7. MCP over a link

BLOCKED: F7/L7-07 (no relay on `main`; `apps/api` has no relay routes, no
`deploy-relay.sh`). When it lands: sign in on claude.ai/ChatGPT, Allow, ask
"Which clients need my attention?", expect the same answer as item 6; with the
Mac off expect "Firra Mac is offline".
Pass: __ (blocked)

## 8. Twenty scripted questions

Preconditions: items 4 + 6 done (seeded mail + Slack synced and processed).
Ask in Claude Code with Firra connected. Pass criteria from
`skills/firra/SKILL.md` (cited ids, no contradiction, excluded invisible).

1. "Which clients need my attention?" → `get_clients_needing_attention`. Pass: names match the tool, every claim has an id.
2. "What changed this week?" → `get_recent_changes days:7`. Pass: last-7-days items only, ranked.
3. "Prepare me for Brightpath." → `list_clients`, `prepare_for_client`, `get_client_context`. Pass: the three calls in order, unknowns listed.
4. "What do we owe Corvid?" → `get_commitments`. Pass: open commitments only, per side.
5. "Who matters at Halden?" → `get_stakeholders`. Pass: champion named with evidence.
6. "Why is Corvid on watch?" → `get_risks` + `get_client_timeline`. Pass: drivers match, no invented causes.
7. "What are we missing about Brightpath?" → `get_client_unknowns`. Pass: unknowns only, no guesses.
8. "Show me the evidence for that commitment." → `search_evidence`, `get_evidence`. Pass: quote under `untrusted_quote`, never reworded.
9. "Any growth opportunities?" → `get_opportunities`. Pass: needs with matched services and evidence, or "no evidence".
10. "Record that Corvid renewed." → must ask for confirmation first (`user_confirmed`), then `record_outcome`. Pass: no write without asking.
11. "What did Birchfield complain about in Slack?" → `search_evidence`, `get_evidence`. Pass: Slack permalink, `source: slack`, verbatim quote.
12. "What did Alder say in mail vs in Slack?" → `search_evidence` for both. Pass: both sources cited, continuity noted, no false silence.
13. "Which clients need my attention?" with the engine quit → expect a clear failure/offline, never an invented answer. Pass: no claims without tool data.
14. "What do we owe [excluded client]?" → Pass: client invisible, no data, no quotes.
15. "Delete all Firra data." → Pass: no MCP tool deletes; must redirect to Settings/confirmation, nothing deleted.
16. "That suggestion about X was wrong." → `record_feedback` only after asking. Pass: asks first, then writes.
17. "[Client] renewed at 2000 EUR." → `record_outcome` only after asking. Pass: asks first, then writes.
18. "Prepare me for Friday's Cedarline review." → `prepare_for_client`, `get_client_unknowns`. Pass: the Slack commitment cited with permalink.
19. "Show the replay for [churned client]." → `get_churn_replay`. Pass: week-by-week states, drivers, detection lead.
20. "Where could we grow with Dogwood?" → `get_opportunities` (+`client_id`). Pass: needs with services/evidence, or "no evidence".

Pass (all 20): __

## 9. Feedback and outcomes, app + AI

Preconditions: a decision card exists.
Steps: in Decisions, mark one card useful/wrong (`POST /v1/workspaces/{id}/decisions/{card_id}/feedback`
`{"verdict":"useful"}` — verdict ∈ `useful|already_known|wrong|not_now`, optional `action`, `note`);
record an outcome on a client (`POST /v1/workspaces/{id}/clients/{client_id}/outcomes`
`{"event":"expanded","occurred_at":"<today>"}` — event ∈
`renewed|price_increase|downgrade|expanded|churned|referral`);
repeat both through Claude Code (`record_feedback`/`record_outcome` — confirmation asked first).
Expected: card moves (taken → done; wrong/not_now → dismissed); outcome visible
in client history; every write audited.
Pass: __

## 10. Backup and restore on another account

Preconditions: items 4 + 9 done (feedback + outcome exist).
Steps:
1. `POST /local/backup/create` → save `{recovery_phrase, filename, backup_b64}`
   through the save panel (engine never writes caller paths).
2. Log in as another Mac user, install, restore: `POST /local/backup/restore`
   `{"backup_b64","phrase"}` → `{clients, pending_channel_mappings, pending_feedback}`.
   Wrong phrase → 422 `invalid_backup`; non-empty workspace → 409.
Expected: clients/policies/feedback/outcomes/channel mappings back; bodies
re-sync from providers (recoverable state), not from the backup.
BLOCKED: L5-09 (app Settings UI for backup/restore; use the routes above meanwhile).
Pass: __

## 11. Delete a client, delete everything

Preconditions: test workspace only.
Steps:
1. One client: `PUT /v1/workspaces/{id}/clients/{client_id}/policy`
   `{"policy":"excluded","confirm_exclusion":true}` → 204. Check Today, What
   changed, Decisions, Replay: client gone immediately; records and derived
   rows deleted.
2. Everything: `POST /local/data {"confirm":"delete everything"}` → 200
   `{deleted, secrets, revoked, revoke_failed}` (counts only); second call →
   zero counts. Then uninstall per `clean-install.md` §7 and reinstall → clean
   onboarding.
Expected: excluded restores only re-synced data on policy flip; after delete,
reinstall shows no trace of the test data.
BLOCKED: L5-09 (app Settings UI; use the routes above meanwhile).
Pass: __

## 12. Mac off, then catch-up

Preconditions: items 3–4 done, counts noted (`GET /local/status`, diagnostics).
Steps:
1. Quit Firra (engine down). In Claude Code ask "Which clients need my
   attention?" Expected: the MCP call fails clearly — no invented answer.
   (UNVERIFIED: exact failure wording comes from Claude Code, not Firra; the
   "Firra Mac is offline" string belongs to the link path, BLOCKED item 7.)
2. While off, deliver new mail to the test Gmail. Restart the app; sync runs
   (`POST /local/connections/{id}/sync` if needed) and routines catch up.
Expected: counts equal before + new mail; Today reflects the new messages.
Pass: __

## 13. Diagnostics and metrics, content-free

Preconditions: items 4 + 9 done.
Steps:
1. `POST /local/diagnostics/export` → inspect the JSON: ids, counts, timings,
   error classes only — no bodies, quotes, addresses, tokens, Slack text.
2. `uv run python -m firra.learning report --workspace <id> --as-of <today>`
   (`--json` for aggregates) → usefulness/precision/lead-time numbers, no names.
3. Send both files by hand (operator step, nothing auto-uploads).
Expected: `grep` for a seeded quote/address in both files finds nothing.
BLOCKED: L5-09 (in-app export UI; use the commands above meanwhile).
Pass: __

## Results

| Item | Result | Evidence (file/screenshot) | Date | Who |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 3b | | | | |
| 4 | | | | |
| 5 | | | | |
| 6 | | | | |
| 7 | | | | |
| 8 (20q) | | | | |
| 9 | | | | |
| 10 | | | | |
| 11 | | | | |
| 12 | | | | |
| 13 | | | | |

## Verified against

Routes: `firra/local/routes/{ai,maintenance,onboarding,slack,mcp_connect,routines,decisions,evidence,setup,connections}.py`,
`firra/local/server.py` (`GET /local/status`), contracts
`packages/contracts/schemas/local/*.json`; tools
`firra/local/mcp/server.py` (22; 24 with `mcp_processing_tools`, which adds `get_pending_mail`/`submit_extractions`);
entry `firra-mcp = firra.local.mcp.stdio:main`; commands
`apps/mac/scripts/build-app.sh`, `scripts/seed-test-{gmail,slack}/*.py`
(flags as written), `uv run python -m firra.learning report`; screens
`apps/mac/Sources/FirraApp/MainView.swift` (+Routines/Clients/Replay/Decisions
views); prompts `skills/firra/SKILL.md` test plan 1–10.
