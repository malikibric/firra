# Firra Local v1 — build plan

Adopted 2026-10-05 (owner decisions in §2.1). Replaces the cloud-first architecture of `docs/architecture.md` v1 for everything
that touches client data. Written by the lead (Claude); executed by Codex (implementation), Muse Spark (research, independent
tests, QA, docs) and Claude (contracts, security, OAuth, relay, review, merging). Workflow: the original task spec.
Live status: the original task spec. Marks: **[estimate]** = lead's estimate; **UNVERIFIED** = settled by a research task or a spike
before we build on it.

## 0. Summary

- **Product:** Firra runs entirely on the user's Mac. Mail, calendar and Slack are read from the user's own accounts; facts, memory,
  signals, assessments, routines and notifications live in a local database. The user's **own AI** (Claude Code or Codex in the
  background, or any MCP client in conversation) does the extraction and the talking; Firra **verifies every claim** and decides
  states, plays and importance deterministically. No Firra server ever stores or sees client data.
- **Connect once:** one Gmail/Calendar and one Slack connection on the Mac feed one local database that powers both Firra's continuous
  client-intelligence engine and the MCP tools of the user's preferred AI (adopted 2026-10-05, `docs/plans/local/source-layer.md`).
- **MCP** has two layers over that database: **intelligence** (Firra's states, risks, opportunities, commitments, meeting prep, with
  evidence) and **client-scoped sources** (search and bounded excerpts of one client's mail, Slack and calendar, off until the owner
  switches it on). It works locally (stdio, for tools on the same Mac) and through a public link (a relay in our small cloud, OAuth) for
  claude.ai (web, mobile, desktop) and ChatGPT. When the Mac is off the client gets a clear "Firra Mac is offline".
- **Sources in v1:** Gmail, Google Calendar and **Slack** (through a Slack app the user creates in their own workspace).
- **Single user, no teams.** Firra sees only the owner's mailbox and Slack; assessments carry a participant-coverage caveat.
- **Duration:** about 7 weeks (35 working days) **[estimate]**, 12 phases, checkpoints G-A to G-E and G-S.
- **No GitHub Actions minutes:** all checks run on the Mac with `scripts/check.sh`; no pull requests; the lead merges.

## 1. Definition of done

Firra Local v1 is done when, on a clean Apple Silicon Mac (macOS 14+), all of this passes:

| # | A user can | Checked by |
|---|---|---|
| 1 | Download the DMG, install (right-click → Open), start the app | clean-install protocol (L6-04) |
| 2 | Go through onboarding: privacy explanation, connect Google, choose clients and policies, choose AI processing | E2E protocol (L10-01) |
| 3 | Have mail and calendar pulled (3 months by default, up to 12) with no mail lost | test Gmail |
| 3b | Connect Slack (optional): create their own Slack app from one link, paste the token, pick channels and map them to clients; Slack messages become evidence with a permalink; a client moving from mail to Slack does **not** look like falling engagement | test Slack workspace, Northbeam Slack scenarios (G-S) |
| 4 | Get extraction through their own Claude Code or Codex in the background, or in conversation if they have neither | executor eval (L3-06) |
| 5 | See Today, Clients, Client detail, Replay, Routines, Mail; notifications only for *important*, at most 3 per week | existing tests and the T49e routine eval stay green |
| 6 | Connect the **local MCP** (Claude Code, Cursor, Codex, Claude Desktop) with one click or one command | S4 client matrix |
| 7 | Connect the **MCP over a link** in claude.ai (web, mobile, desktop) and ChatGPT with sign-in and "Allow" | interop protocol (L7-07) |
| 8 | Ask the scenario questions (morning review, meeting prep, …) and get answers with evidence and links | 20 scripted questions (L4-05) |
| 8b | With source access switched on, ask "what exactly did <client> say about …" and get bounded excerpts of that client's mail, Slack and meetings with links, never another client's or a colleague's text | source questions (S7), Layer 2 oracle (S3o) |
| 9 | Give feedback and record outcomes in the app and through their AI | write tests |
| 10 | Make an encrypted backup of the non-recoverable state and restore it on another Mac account | restore test (L8-02) |
| 11 | Delete a client or all Firra data, verified | deletion tests (L8-03) |
| 12 | With the Mac off, get a clear "offline" from Claude; after wake-up everything catches up | S5 + E2E |
| 13 | Export diagnostics and metrics without content and send them to us by hand | content-free tests (L8-04/05) |

**Product rules stay** (`CLAUDE.md` §2): evidence or nothing, no numeric score, no employee scoring, text only, drafts never sent,
per-client data policy, minimization.

**New rules L0-01 writes into `CLAUDE.md`:**
1. No client data ever goes to Firra's cloud. The cloud holds only accounts, devices, OAuth grants and the release feed.
2. Firra verifies **every** output of the user's AI (quote verbatim in the source, format, policy) before storing it. The AI
   proposes; Firra accepts or rejects.
3. The AI executor runs with **no tools and no file access**: text in, JSON out (L3-08).
4. Secrets live only in the Keychain and in the engine's memory. Never plain on disk, never in logs.
5. Local sockets and token files are user-only (0600/0700). No TCP except 127.0.0.1 with a token.
6. Logs hold ids, counts, durations and error classes. Never bodies, quotes, addresses, tokens or Slack text.

## 2. Decisions

### 2.1 Owner

| Decision | Value |
|---|---|
| Architecture | Fully local, one user, no teams |
| Extraction | The user's AI (D1) |
| MCP | Local and through a link (relay) for Claude and ChatGPT |
| "Always available" mode | No |
| Apple Developer / notarization | Not now |
| G1 (test on real mail before building) | Skipped; first real mail is the first pilot partner |
| Project rules | Rewrite in step one |
| Domain | None yet: temporary Cloud Run URL; buying one is an owner task before F7 |
| Sources in v1 | Gmail, Google Calendar, **Slack** |

### 2.2 Lead

| # | Decision | Why |
|---|---|---|
| T1 | One Python package `firra` stays and gains `firra/local/` (runtime, server, MCP, executors). Cloud-only parts are deleted in F9. No fork. | least rewriting; tests stay |
| T2 | **Local PostgreSQL 17 + pgvector**, bundled in the app, unix socket only, no TCP | the 30 migrations and 50 SQL functions run unchanged |
| T3 | **Supabase shim** (`supabase/local/shim.sql`) provides `auth.users`, `auth.uid()`, roles and the `extensions` schema; then the **same** migrations in `supabase/migrations` run. | migrations depend only on `auth.users` (14 places), `auth.uid()` (8), `extensions.gen_random_bytes` (3) and `vector` (1); one source of truth |
| T4 | The engine is a **child process of the app**, not a separate LaunchAgent. The app is a login item living in the menu bar. | without an Apple Developer account Keychain access from a separate process is fragile; the app holds secrets and hands them to the engine in memory |
| T5 | The existing job queue (`job` table + `REGISTRY` handlers) stays; a local executor replaces Cloud Tasks inside the same process | all handlers and their tests stay |
| T6 | Local server: FastAPI + uvicorn on 127.0.0.1, random port, bearer token | no new runtime |
| T7 | MCP on the Mac: official Python `mcp` SDK (new dependency, approved here) | same tool names and schemas as today |
| T8 | The Mac app keeps its hand-written `APIClient`; only the address and token change | 433 lines keep working |
| T9 | **No model in Firra:** routine cards are already deterministic; weekly cards (`cards.v1`) and meeting briefs (`meeting.v1`) become deterministic facts; the user's AI writes the words when asked | D1; fewer moving parts |
| T10 | Latent needs without embeddings: extraction adds a `topic_key` to requests and the engine groups by it; embedding columns stay, unused | no embedding model; A2 is parked anyway |
| T11 | Mail and chat bodies encrypted in the database (AES-GCM, key from the Keychain, in the repo layer) **plus** FileVault required (warning if off) | two layers |
| T12 | Apple Silicon only, macOS 14+ | one binary architecture |
| T13 | Ad-hoc signature of all bundled binaries (the existing local certificate "Firra Local Signing" for the app, `apps/mac/README.md`). DMG and `latest.json` feed on a public EU GCS bucket with SHA-256. | no Apple account; repo is private so GitHub Releases are not public |
| T14 | **Everything local: checks, DMG build, relay deploy.** `scripts/check.sh` does what `ci.yml` does; DMG by script on the Mac; relay deployed from the Mac with `gcloud run deploy --source` (Cloud Build, not Actions). | few Actions minutes; macOS minutes cost 10x |
| T15 | Relay in `apps/api` (Hono, Cloud Run, one instance for the pilot), WebSocket through the `ws` library (new dependency, approved here) | `apps/api` already has MCP transport, Origin checks, rate limit, PAT |
| T16 | OAuth per `docs/plans/f-mcp-oauth.md`: Supabase OAuth server spike first, otherwise our own authorization server with a vetted library (L7-02) | the plan exists |
| T17 | Google OAuth: **Desktop app** client, loopback redirect + PKCE, run by the Mac app in the system browser | Google's recommendation for desktop (UNVERIFIED, S1) |
| T18 | Backfill default **3 months**, up to 12 by choice; Replay for a lost client on demand | saves the user's AI subscription |
| T19 | Outlook, Microsoft, domain-wide delegation, contributors, teams are **deleted** from the code in F9 (they stay in git history) | no dead code |
| T20 | **Test Gmail account** filled with Northbeam through the Gmail API import with original dates (UNVERIFIED, L2-04) | real Gmail flow without real client data |
| T21 | **All automatic Actions triggers are off** (done 2026-10-05): staging deploy on push and the weekly evals cron; only manual `workflow_dispatch` remains. `ci.yml` runs only on PRs, and we open none. | 0 minutes; `main` can change freely |
| T22 | **Slack through the user's own internal Slack app.** Firra gives a link with a ready manifest; the user creates and installs the app in their workspace and pastes the **user token** into Firra (Keychain). No Firra-distributed Slack app, no Events API, no Socket Mode, no Firra server in the path. | Events API needs a public URL (messages would transit our relay); distributed non-Marketplace apps have very strict history limits (UNVERIFIED for internal apps, L2S-01); Socket Mode would put our app-level token on every Mac |
| T23 | **Slack sync = polling** of mapped channels (every 5 min: `conversations.history`, plus `conversations.replies` for threads whose `latest_reply` changed) and a daily re-scan of the last 7 days for edits and deletions. Fallback and backfill: Slack export `.zip` import. | simplest thing that works; few mapped channels (10–30) |
| T24 | **Slack minimization:** only mapped channels (channel → client) and direct messages with people of a mapped client are stored; internal channels only if the user opts them in per client; calls and huddle audio never ("text only"); files and canvases not in v1 | existing minimization and policy rules apply as for mail |
| T26 | **Two MCP layers over one database** (`docs/plans/local/source-layer.md`): intelligence (derived, Firra's judgement) and client-scoped sources (raw, untrusted). Layer 2 never feeds Layer 1; raw text is not evidence. | the product promise "connect once, power both" without turning Firra into a mail gateway |
| T27 | **Sources are read from the synced local database, not live from Gmail/Slack.** `refresh_client_sources` asks the normal sync to run now. | one place enforces minimization; live Gmail search is scoped only by an AI-written query, Slack search needs `search:read` and spans the workspace |
| T28 | **Every source call names one mapped client**; no cross-client or inbox-wide search; excerpts ≤ 280 chars, ≤ 8 hits, ≤ 8,000 chars per answer; context ≤ 3 neighbouring messages, never a whole thread or attachments | client scope and prompt-injection surface |
| T29 | **Source access is off until the owner switches it on** (`mcp_source_access`); over a link the `sources` scope needs a separate consent, unchecked by default; intelligence tools are renamed now (`get_commitments`, `get_opportunities`), no aliases | it is the first path that sends mail text to an AI provider; no external users yet |
| T25 | **Signals across channels:** engagement is computed across all channels together (mail + Slack), reply latency has **separate thresholds per source** (Slack minutes, mail hours). New `rule_version`, oracle cases, Northbeam Slack scenarios. | otherwise a client who moves from mail to Slack looks like falling engagement; a false alarm is worse than none |

## 3. How we work

See `CONTRIBUTING.md`. In short: one branch per work package, small commits, local `scripts/check.sh`, a pull request into `main` once
`scripts/check.sh all` is green, and security-sensitive packages (🔒) need independent oracle tests first. Work package: 1–2 days, diff ≤ ~400 lines.
Stop and ask on anything that changes the design. 

## 4. Roles

| Who | Does | Does not |
|---|---|---|
| **Claude (lead)** | rules, contracts, security-sensitive code (secrets, OAuth, relay, executor sandbox, encryption), migrations, briefs, review, merging, security review, daily report | merge without checks |
| **Codex** | main implementation: engine, connectors, executors, read models and MCP port, Mac screens, packaging, relay code, cleanup | change contracts, migrations or security decisions without the lead |
| **Muse Spark** | sourced research, independent tests (oracles written from the spec, not from the code), QA protocols, documentation, drift checks | write production code that it then tests itself |

## 5. Phases and checkpoints

```
Week      1          2          3          4          5          6          7
F0 Prep   ███
F1 Engine   ████████
F2 Google          ███████
F2b Slack  ░(research)       ████████████
F3 AI work            ██████████
F4 Read+MCP           ██████████
F5 Mac app                     ██████████
F6 Package ██(spike)           ██████
F7 Relay+OAuth        ███(spike) ████████████
F8 Hardening                              ███████
F9 Cleanup                                     ████
F10 Release                                       █████████
Gates:    G-A(d3)  G-B(d10)  G-C(d15)  G-S(d24) G-D(d25)        G-E(d35)
```

| Gate | When | Condition to continue | If it fails |
|---|---|---|---|
| **G-A** | day 3 | S2: Python + Postgres start from the bundle on a clean environment. S1, S3a, S4a have answers (Slack research L2S-01 lands on day 4 and opens F2b). | packaging: fallback R1; research: adjust phases, tell the owner |
| **G-B** | day 10 | Engine local on recorded fixtures: sync → extraction (fake executor) → signals → assessment → `/v1` read. All Python tests green on local Postgres. | F5 does not start |
| **G-C** | day 15 | On the test Gmail: real Gmail sync → extraction through real Claude Code → MCP stdio answers in Claude Code with evidence | fix before Mac work |
| **G-S** | day 24 | Slack: test workspace connected through own app, mapped channels sync, Slack messages pass extraction and quote verification, Northbeam Slack scenarios and multi-channel signals green, T49e routine eval still green | pilot may start with Gmail only; Slack follows when G-S passes |
| **G-D** | day 25 | Mac app: whole onboarding on the engine. MCP over a link works from claude.ai (web or desktop at least). | release without ChatGPT if only it is the problem |
| **G-E** | day 35 | All items of §1 pass (including 3b). Security review closed. DMG works on a clean Mac. | pilot waits |

## 6. First week

| Day | Claude | Codex | Muse Spark |
|---|---|---|---|
| 1 | L0-01 rules, L0-02 workflow and `check.sh`; merged #162–#164; briefs | L0-07 packaging spike (S2) | L0-04 Google research (S1) |
| 2 | L0-01 done, L0-03 contracts | L0-07 continues | L0-05 MCP clients (S4a) |
| 3 | L0-03 done, **G-A** | L0-07 report, **G-A** | L0-06 AI CLI (S3a), **G-A** |
| 4 | L1-01 shim + migrations 🔒 | L1-02 Postgres supervisor | L2S-01 Slack research |
| 5 | L1-06 secrets handshake 🔒 | L1-04 job executor | L1-08 foundation oracle (from the spec) |

## 7. Work packages

Format: **ID · name** · owner · depends on → delivery · acceptance. 🔒 = security-sensitive: the independent oracle must be green before merge.

### F0 · Preparation (days 1–3)

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| L0-01 | Rewrite project rules | Claude | — | New `CLAUDE.md`, `AGENTS.md`, `docs/architecture.md`, `docs/north-star.md`, `docs/roadmap.md`, this plan; old documents in the earlier design (not included) | Muse Spark drift check: no old rule contradicts a new one |
| L0-02 | Workflow and local checks | Claude | — | the original task spec; `scripts/check.sh <area>` (local copy of `ci.yml`, by area); gitleaks and semgrep installed locally (dev tools, not product dependencies); automatic triggers off | `check.sh all` green on `main`; a push to `main` starts no workflow |
| L0-03 | Contracts for the local world 🔒 | Claude | L0-01 | `packages/contracts`: local routes (engine status, onboarding, Google connect, AI settings, backup, diagnostics), MCP tool schemas (+ new tools), the app↔engine protocol (secret handshake), relay frame protocol; `docs/plans/local/contracts.md` | generated zod/Pydantic types compile; contract tests green |
| L0-04 | **S1** Google research | Muse | — | `docs/research/google-local.md` | lead checks the sources |
| L0-05 | **S4a** MCP client matrix | Muse | — | `docs/research/mcp-clients.md` | every claim has a source |
| L0-06 | **S3a** AI CLI research | Muse | — | `docs/research/ai-cli.md` | lead checks the sources |
| L0-07 | **S2** packaging spike | Codex | — | Postgres 17 + pgvector and relocatable Python in a bundle, smoke script, numbers | runs on a clean environment without Homebrew; sizes and times recorded |
| L0-08 | Freeze the cloud | Claude | — | record what runs in staging and what it costs; no new cloud work except the relay | note on the board |

### F1 · Engine foundation (days 4–10)

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| L1-01 | Shim + local migrations 🔒 | Claude | L0-07 | `supabase/local/shim.sql`; `firra.local.db.migrate` (applies `supabase/migrations` in order, applied-versions table) | all migrations run on local Postgres; **pgTAP suite green locally**; re-run is harmless |
| L1-02 | Postgres supervisor | Codex | L0-07 | `firra.local.pg`: `initdb` on first run (`~/Library/Application Support/Firra/pg`, 0700), start on a unix socket only (`listen_addresses=''`), clean stop, recovery after a crash | tests: first start, restart, `kill -9` recovery, no TCP listener |
| L1-03 | Local identity | Claude | L1-01 | `firra.local.bootstrap`: one workspace, one local user (shim), owner membership, time zone from the Mac | existing RPCs (feedback, outcomes, policy) work through the shim |
| L1-04 | Local job executor | Codex | L1-02 | loop over the `job` table + `REGISTRY`, retry with backoff, recovery of stuck jobs; `tasks/queue.py` → local `enqueue` | tests: idempotency, retry, crash mid-job |
| L1-05 | Local scheduler | Codex | L1-04 | replaces Cloud Scheduler: sweep (sync, processing, routines with catch-up, retention, meeting briefs); wake detection (clock jump) → sweep now | tests with a fake clock: 3 days asleep = one sweep on wake, one run per routine for today whose window (since the last succeeded run, max 7 days) covers the gap, no duplicates |
| L1-06 | Secrets handshake 🔒 | Claude | L0-03 | app → engine over a unix socket at start: Google refresh tokens, encryption key, local API token; the engine keeps them only in memory; `firra/crypto` (KMS) → local key provider (AES-GCM) | tests: no secrets on disk (search the data dir), wrong socket peer refused |
| L1-07 | Local profile and encrypted bodies 🔒 | Codex | L1-06 | `FIRRA_PROFILE=local`: paths, log rotation, no GCS; bodies encrypted on write and decrypted on read in the repo layer (`enc:v1:` prefix) | tests: body unreadable in the database, repo read works, quote verification works |
| L1-08 | Foundation oracle | Muse | L1-01..07 | independent tests: fresh start, restart, crash, clock jump, permissions 0700/0600, no TCP (`lsof`), migrations twice | all green before G-B |
| L1-09 | Port tests to local Postgres | Codex | L1-01 | Python integration tests run against local Postgres (not the Supabase CLI) | whole `pytest` green |

### F2 · Google locally (days 6–11)

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| L2-01 | Desktop Google OAuth 🔒 | Claude | L0-04, L1-06 | Swift: system browser + loopback listener + PKCE; scopes `gmail.readonly`, `calendar.readonly`, `openid email`; tokens in the Keychain; revoke and reconnect | PKCE/state tests, fake Google server |
| L2-02 | Connectors locally | Codex | L1-06, L2-01 | token from memory + refresh, "reconnect" signal to the app, Gmail backfill (3/6/12 months), incremental by polling the history API (5 min), full resync when history expires, Calendar sync tokens; no Pub/Sub | existing fixture tests green + new ones for expired history |
| L2-03 | Client suggestions | Codex | L2-02 | onboarding routes: domains from mail by thread count (without gmail.com and similar), deterministic | fixture tests |
| L2-04 | Test Gmail with Northbeam | Muse | L0-04 | `scripts/seed-test-gmail`: Northbeam mail and calendar into a test Google account via Gmail API import with original dates (UNVERIFIED) + runbook | the test account holds 12 months of Northbeam with exact dates |
| L2-05 | Catch-up oracle (S5) | Muse | L2-02 | tests: history gap, expired historyId → resync, no duplicates, nothing lost after "3 days offline" | green |
| L2-06 | Local file import | Codex | L1-07 | mbox/eml/pst from a local path (no GCS) | existing import tests green |

### F2b · Slack (research days 1–4, main work days 12–24)

How it works for the user: in onboarding (or Settings) they click "Connect Slack" → Slack opens with a ready app manifest →
"Create" and "Install" in their workspace → they copy the user token into Firra → Firra lists channels (Slack Connect channels
suggested by client name or domain) → the user confirms the channel → client mapping and optionally opts in internal channels.

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| L2S-01 | Slack research | Muse | — | `docs/research/slack-local.md` (limits for internal vs distributed apps, API terms and AI use, creation from a manifest, scopes, Slack Connect visibility, export format) | lead checks the sources. **If the terms forbid our use, stop and tell the owner before building.** |
| L2S-02 | Schema and contracts 🔒 | Claude | L2S-01, L0-03 | migration: `connection.provider` + `slack`, `slack_upload`; canonical chat `Message` (channel, `ts`, `thread_ts`, author, permalink); existing: `source_record.kind = chat`, `connection_scope.kind = channel`, `person_identity.kind = slack_id` | pgTAP green; codegen current |
| L2S-03 | Slack manifest and connect 🔒 | Claude | L2S-01 | manifest (read scopes only), creation link, token validation (`auth.test`), token in the Keychain via the handshake (L1-06), disconnect deletes and revokes it (`auth.revoke`) | tests with a fake Slack server; wrong or too-broad token refused |
| L2S-04 | Slack connector | Codex | L2S-02, L2S-03, L1-04 | `firra/connectors/slack/`: channel list, backfill of mapped channels, polling every 5 min, threads via `conversations.replies` when `latest_reply` changes, daily 7-day re-scan (edit → new `content_hash` and reprocess; delete → delete record and derived data), `Retry-After`, permalinks | recorded-fixture tests: backfill, thread, edit, delete, rate limit, revoked token |
| L2S-05 | Slack text cleaning | Codex | L2S-04 | `firra/clean`: mrkdwn → text, `<@U…>` → name, links, emoji codes, code blocks, quoted messages; bots and system messages dropped | unit tests; quote verification works on cleaned text |
| L2S-06 | Resolution and mapping | Codex | L2S-04 | Slack user → person (`slack_id`, email when visible); external team in Slack Connect = client side of the channel, own team = firm; direction: client → `in`, firm → `out`, internal channel → `internal`; direct messages only with people of a mapped client, the rest dropped **without storage**; channel mapping suggestions | minimization tests: unmapped channel and a DM with a colleague leave no trace |
| L2S-07 | Extraction for chat | Claude | L3-05, L2S-05 | `extract.v6` chat variant: packet = a thread or a channel day, earlier messages as unquotable context; same schema, same verification | fixture tests on Slack messages |
| L2S-08 | Multi-channel signals (spec) 🔒 | Claude | L2S-02 | change `signals.yaml` + spec: engagement across channels, latency and champion per source with their own thresholds, coverage guard for a Slack connection, new `rule_version`; new oracle cases in `oracle.yaml` | spec reviewed |
| L2S-09 | Multi-channel signals (code) | Codex | L2S-08 | implement in `firra/signals` and fact loading (`load_facts`), no future leakage | oracle (old + new) green; leakage test green |
| L2S-10 | Multi-channel oracle | Muse | L2S-08 | independent tests from the spec: client moves from mail to Slack (no false drop), a complaint in Slack opens `delivery_complaint`, a commitment from Slack goes late, champion slower only in Slack | green on the implementation |
| L2S-11 | Northbeam Slack | Claude + Codex | L2S-08 | Claude: Slack scenarios in the spec and truth file (2–3 clients with a Slack Connect channel; one moves from mail to Slack; one complaint and one commitment only in Slack). Codex: Slack message generator in `firra/synthetic`. | perfect-extraction check and T49e green with the new scenarios |
| L2S-12 | Slack export import | Codex | L2S-04, L2-06 | `.zip` export from a local path (backfill or fallback), same minimization | fixture tests |
| L2S-13 | Mac: connect and map | Codex | L2S-03, L5-03 | optional onboarding step and a Settings entry: "Connect Slack", channel list with suggestions, mapping to clients, opt in internal channels, sync status, disconnect | Swift tests + manual QA |
| L2S-14 | Test Slack workspace and E2E | Muse | L2S-04, L2S-11 | script that fills a test workspace with Northbeam messages, runbook, E2E: connect → map → sync → process → evidence with a Slack link in an MCP answer | passes on the test workspace |

Evidence from Slack reaches MCP like evidence from mail: `untrusted_quote`, `source: "slack"`, permalink.

### F3 · AI work through the user's AI (days 6–15)

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| L3-01 | Executor interface 🔒 | Claude | L0-03, L0-06 | `firra/ai/`: `ExtractionExecutor` (input: a packet of records in an untrusted wrapper; output: raw JSON → existing parse + verify). Replaces `Llm` in extraction. `llm_usage` records model and tokens when known, no cost and no budget. | a fake executor passes the whole flow |
| L3-02 | Claude Code executor 🔒 | Codex | L3-01, L3-08 | runs `claude -p` through stdin, JSON output, model choice, timeout, one at a time, detects "not installed / not signed in / limit" → status for the app | tests with a fake CLI binary |
| L3-03 | Codex executor 🔒 | Codex | L3-01, L3-08 | same for `codex exec` | same |
| L3-04 | In-conversation processing (MCP pull) 🔒 | Codex | L3-01 | tools `get_pending_mail` (packet + instructions + lease with expiry) and `submit_extractions` (lease check, verify, store, counts of accepted and rejected). `full` clients only. | adversarial tests: invented quote, someone else's record, `excluded` client, oversize input, expired lease |
| L3-05 | Instructions `extract.v6` | Claude | L3-01 | prompt tuned for Claude/GPT CLIs, same schema + `topic_key` (T10), version and model recorded | fixture tests |
| L3-06 | Executor eval | Muse | L3-02, L3-05 | Northbeam eval over recorded CLI outputs (free); one real run with the owner's go (their subscription or up to 15 USD API). Compare with `status-a.md`. | quote validity 100 % (by construction); recall of risk signals ≥ 0.8 |
| L3-07 | AI work scheduling | Codex | L3-02 | order (active clients, newest first), pause until the limit resets, progress for the app, signals recomputed when content arrives | tests with a fake clock and limits |
| L3-08 | Executor sandbox 🔒 | Claude | L0-06 | CLI with no tools, no MCP, no file access, in an empty temp directory, no inherited env secrets; exact flags from L0-06 | test: a mail saying "run a command / read a file" produces no action, only JSON |
| L3-09 | Cards and meeting brief without a model | Codex | L3-01 | `cards/word.py` and `brief/word.py` → deterministic fact rendering; the notch shows facts | card and brief tests green, `model = deterministic` |
| L3-10 | Needs without embeddings | Codex | L3-05 | `needs` groups by `topic_key`, counts from SQL stay | needs oracle adapted and green |

### F4 · Local read and MCP (days 8–15)

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| L4-01 | Local server 🔒 | Claude | L0-03, L1-06 | FastAPI on 127.0.0.1, random port, bearer token, Host/Origin checks, route framework, read audit | tests: no token → 401, wrong Origin refused |
| L4-02a | Read models: clients, attention, changes, client detail | Codex | L4-01 | port of `apps/api/src/routes/read.ts` to Python, same OpenAPI schemas | TS integration tests ported as Python HTTP tests, green |
| L4-02b | Decisions, feedback, outcomes | Codex | L4-01 | writes through the existing SQL RPCs | same |
| L4-02c | Replay, timeline, routines, meeting briefs, mail | Codex | L4-01 | mail reads decrypted bodies from the engine (replaces the vault) | same |
| L4-02d | Settings: clients, domains, policy, services, Google account, AI settings | Codex | L4-01, L2-03 | no team routes | same |
| L4-03 | MCP server (Python) 🔒 | Codex | L4-02a–c, L3-04 | port of the tools in `apps/api/src/mcp/server.ts` (same names and schemas) + new: `get_client`, `prepare_for_client`, `get_growth_opportunities`, `get_evidence`, `record_feedback`, `record_outcome` (ask for confirmation), processing tools; **5 MCP prompts** (scenarios); rules in the tool descriptions; Streamable HTTP handler (for the relay) + stdio entry `firra-mcp` (token from a 0600 file) | tool contract tests |
| L4-04 | MCP oracle 🔒 | Muse | L4-03 | independent tests: `excluded` invisible, `metadata_only` without quotes, `untrusted_quote` marked, audit per call, writes only through allowed tools, prompt-injection fixtures | green |
| L4-05 | Answer-layer test | Claude | L4-03 | 20 scripted questions in Claude Code over Northbeam; check: no contradiction of the state, no invented facts, quotes with ids | at most 1 minor error in 20, the rest fixed in tool descriptions or the skill |
| L4-06 | Firra skill | Muse | L4-03 | `SKILL.md` (how to use Firra: cite ids, do not change state, untrusted text, meeting-prep order) | review + used in L4-05 |

### F4b · Client-scoped source layer (adopted 2026-10-05; spec `docs/plans/local/source-layer.md`)

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| S0 ✅ | Raw-at-rest fix 🔒 | Claude | — | no plain provider payloads on disk (`NoRawStore`), legacy files removed, mail subjects and meeting titles encrypted, legacy rows re-encrypted at start | data dir (incl. DB files) holds no fixture text — done `36e7f91` |
| S1 ✅ | Contracts and switch 🔒 | Claude | S0 | Layer 2 result schemas; owner setting `mcp_source_access` (off; replaces `mcp_evidence_context`); `intelligence`/`sources` scope check in the tool layer; `contracts.md` §5 rewritten for two layers | contract tests; tools refuse when off |
| S3o ✅ | Layer 2 oracle 🔒 | Muse → Claude (Muse out of usage) | S1 | independent tests written before S3: client scope, `excluded` invisible, `metadata_only` without text, caps, `untrusted_text`, hostile fixtures, audit without query text, switch off, refresh throttled | green on S3 |
| S2 ✅ | Source search core | Claude (Codex quota) | S1 | `firra/sources/` (decrypt-and-match for one client, excerpts, caps, policy) + `firra/db/local_sources.py` | ≤ 300 ms for 5,000 records |
| S3 ✅ | Layer 2 tools | Claude | S2 | `search_client_sources`, `get_source_context`, `get_client_timeline` (+ source activity), `refresh_client_sources`, `get_sync_status` | S3o green |
| S4 ✅ | Retention (was L8-01) | Claude | — | bodies 12 months by default, owner setting; search reports its window | tests |
| S5 ✅ | Layer 1 names | Claude | — | `get_client_context`, `get_risks`, `get_opportunities`, `get_commitments` (done as renames: `get_client` → `get_client_context`, `explain_client_change` → `get_risks`, `get_open_commitments` → `get_commitments`, `get_growth_opportunities` → `get_opportunities`; no composites needed, the read models already combine the parts) | catalog tests; L4-04 oracle updated |
| S6 ✅ | Mac switch | Codex | S1 | Settings and onboarding: "Let my AI read excerpts of client mail and Slack" with the plain explanation; Connect AI tools text | Swift tests |
| S7 | Skill and answer test | Muse + Claude | S3, S5 | `SKILL.md` two-layer rules; L4-05 + 8 source questions | ≤ 1 minor error |
| S8 ◐ | Link scopes | Claude | F7 | `sources` scope on the connector link (stage 1, done: `x-firra-scopes` set by the engine from the link, tools refuse without it) and later in the OAuth consent (stage 2); engine check | relay oracle (L7-06o) |

Order: S0 → S1 → S3o → S2 → S3 → S6 → S7; S4 and S5 in parallel; S8 with F7.

### F5 · Mac app on the local engine (days 14–22)

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| L5-01 | Engine lifecycle 🔒 | Claude | L1-06, L4-01 | start the engine from the bundle, secrets handshake, health check, restart with backoff, stop on quit, login item (`SMAppService.mainApp`; UNVERIFIED with an ad-hoc signature), FileVault check | Swift tests + manual crash test |
| L5-02 | APIClient to the local engine | Codex | L5-01 | local address + token; local use needs no sign-in (sign-in only for MCP over a link) | existing Swift tests green |
| L5-03 | Onboarding | Codex | L5-02, L2-01, L2-03 | welcome → privacy (what goes where, AI plan warning) → connect Google → backfill depth → clients and policies → AI processing (detected Claude Code / Codex / "only in conversation") → first results → connect AI tools | view-model tests + manual QA |
| L5-04 | Remove team UI | Codex | L5-02 | no Team, Contributors, ContributorHome; Connections → "Google account" (status, reconnect, disconnect); clean Models/ViewModels | Swift tests green, no dead code |
| L5-05 | Statuses and notifications | Codex | L5-02, L3-07 | menu bar: sync state, AI queue ("37 emails waiting"), executor errors, relay state; notifications work with the local engine | NotificationPlanner tests + manual QA |
| L5-06 | "Connect AI tools" screen | Codex | L0-05, L4-03 | buttons: Claude Desktop (`.mcpb`), Claude Code (command), Cursor and VS Code (deeplink), Codex, claude.ai and ChatGPT (link, from F7) | manual check of each button |
| L5-07 | Mail and evidence from the engine | Codex | L4-02c | MailVaultView and the evidence view read the engine; old VaultStore/VaultSync/VaultKey deleted | Swift tests |
| L5-08 | App QA protocol | Muse | L5-03..07 | manual test script for onboarding and the main screens | passes on the Northbeam test Gmail |

### F6 · Packaging and distribution (spike days 1–3, main days 18–23)

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| L6-01 | App build | Codex | L0-07, L5-01 | `apps/mac/scripts/build-app.sh`: Swift release build, bundled engine (Python, Postgres, `firra` package), ad-hoc signature inside out, DMG; runs locally (T14) | DMG works on a clean account |
| L6-02 | Distribution and feed 🔒 | Claude | L6-01 | public EU GCS bucket (Terraform): DMG, SHA-256, `latest.json`; the app checks daily and reports a new version | feed and hash check tested |
| L6-03 | Upgrade | Codex | L6-01, L8-02 | a new version keeps the data; migrations at start; automatic backup of the non-recoverable state before migrating; Postgres 17 pinned for v1 | test: version N → N+1 with data |
| L6-04 | Clean-install protocol | Muse | L6-01 | new macOS user: install, Gatekeeper, onboarding, restart, upgrade, uninstall (app + data dir + Keychain) | report with results |

### F7 · Relay and OAuth: MCP over a link (spike days 11–13, main days 14–25)

> **Stage 1 (built 2026-10-05, `docs/plans/local/relay.md` §8):** the relay is a small Python service (`services/relay`, FastAPI + `websockets`; the `ws` package is blocked by the pnpm trust policy) and MCP over a link works with **connector links** (secret URL, hash-only on the Mac, scopes, revocation) instead of accounts and OAuth. Stage 2 (OAuth, `node-oidc-provider` per `docs/research/oauth-server.md`, owner approval needed) replaces links for claude.ai and ChatGPT one-click connect. L7-01 (protocol), L7-03/L7-04 (relay and engine side, stage 1) are done; L7-06 oracle in progress; L7-08 deploy script exists, not run.

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| L7-01 | Relay protocol and threat model 🔒 | Claude | L0-03, L0-05 | frames over WebSocket (device auth, request multiplexing, streaming, size limits, timeouts, heartbeat, reconnect); device registration: key pair on the Mac, public key registered through a signed-in session, the device proves itself by signature | document + review by Muse |
| L7-02 | OAuth server 🔒 | Claude | #163, L0-05 | spike of the Supabase OAuth server against the criteria in `f-mcp-oauth.md`; if it fails, our own authorization server with a vetted library; grant bound to user, device and resource; minimal consent page in `apps/api`; DCR or CIMD per L0-05 | criteria of #163 |
| L7-03 | Relay in `apps/api` 🔒 | Codex | L7-01, L7-02 | `/mcp` → token check → find device → forward → return; "offline" as a clear MCP error; rate limit; no content logging; `/.well-known/oauth-protected-resource`; one instance (limit: hundreds of Macs) | integration tests with a fake device |
| L7-04 | Engine side 🔒 | Codex | L7-01, L4-03 | device registration (sign-in through the existing SupabaseAuth in the app), outbound WebSocket client (reconnect, backoff, heartbeat), forwarding to the local MCP handler; **the engine validates the OAuth token itself** (JWKS cached); revocation checked by the relay immediately | tests with a fake relay |
| L7-05 | Control-plane database 🔒 | Claude | L7-02 | migrations: `device`, grants (if Supabase does not hold them); RLS, pgTAP; no table holding client data | pgTAP green |
| L7-06 | Relay and OAuth oracle 🔒 | Muse | L7-03, L7-04 | wrong audience, revoked grant, someone else's device, replay, oversize frames, slow link, fake device, offline behaviour, the test list of #163 | green |
| L7-07 | Interop | Claude + owner | L7-03, L7-04 | connect from claude.ai (web, mobile, desktop), ChatGPT, Claude Code over the URL; recorded traces; the owner's accounts, the owner clicks "Connect", Claude drives | 5 tools work from every place, revocation is immediate |
| L7-08 | Relay deploy | Claude | L7-03 | `scripts/deploy-relay.sh` from the Mac: `gcloud run deploy --source` (Cloud Build, not GitHub Actions), control-plane migrations with `supabase db push`, health check; worker deploy and `deploy.yml` removed in F9 | deploy works from the Mac, 0 Actions minutes |
| L7-09 | Public pages | Codex | L7-03 | from `apps/api`: download, privacy policy, EULA, consent page; text from a lawyer | pages reachable; Google consent screen has the URLs |

### F8 · Hardening (days 21–27)

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| L8-01 | Local retention (now F4b S4) | Codex | L1-05 | bodies kept 12 months by default, a "delete earlier" setting, `excluded`/`metadata_only` through the existing `set_client_policy` | tests |
| L8-02 | Backup and restore 🔒 | Claude | L1-07 | export of the non-recoverable state (feedback, outcomes, mappings, policies, manual corrections, services, routine settings), versioned JSON encrypted with a recovery phrase key (scrypt + AES-GCM, `cryptography`); restore on a new Mac, then resync and re-extraction | round-trip test; wrong phrase refused |
| L8-03 | Deletion | Codex | L1-01, #164 | "Delete client" (existing `excluded` path) and "Delete everything" (data dir + Keychain + Google token revoke + Slack token revoke + device sign-out) | tests: nothing remains |
| L8-04 | Diagnostics | Codex | L1-07 | `firra doctor` and "Export diagnostics" in the app: versions, counts, error classes, logs without content | test searches the export for fixture quotes and addresses and finds none |
| L8-05 | Metrics | Codex | L4-02b | `firra metrics export`: verdicts per family and play, *important* per week, lead time | test: no content and no client names |
| L8-06 | Log review | Muse | L8-04 | extend the log-hygiene test to the engine, executors, Slack connector and relay | green |
| L8-07 | Security review 🔒 | Claude | F1–F7 | threat model + checklist (sockets, token files, Keychain, relay, executor sandbox, prompt injection, Slack token handling, update integrity); findings fixed or recorded as accepted risk | no open critical finding |

### F9 · Cleanup (days 26–28)

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| L9-01 | Delete the cloud data plane | Codex | G-D | Cloud Tasks and OIDC, FastAPI task endpoints, KMS, GCS, Pub/Sub watch, Vertex backend, prices and budget, DWD/org, Outlook/Microsoft, contributors. In `apps/api`: read, setup, contributor, connect, webhooks, uploads, vault routes. | all tests green, SQL guard adapted |
| L9-02 | Terraform and staging | Claude | L9-01 | remove worker, Tasks, Scheduler, Pub/Sub, KMS, data buckets; keep `apps/api` (relay), release bucket, Secret Manager, WIF; **deleting staging data needs the owner's explicit approval** | `terraform plan` reviewed |
| L9-03 | Documentation | Muse | L9-01 | runbooks rewritten or archived (local-dev, mcp-partner-guide, onboarding, e-exit-measurement → local metrics, routines-staging → archive); `evals.yml` without Vertex | drift check with no findings |

### F10 · Release candidate and pilot readiness (days 28–35)

| ID | Name | Who | Depends | Delivery | Acceptance |
|---|---|---|---|---|---|
| L10-01 | E2E protocol | Muse | all | on the test Gmail and test Slack workspace: install → onboarding (Google + Slack) → backfill → processing through Claude Code → Today shows the expected clients → routines (time shift) → MCP stdio and link answer 10 questions with evidence from mail and Slack → backup and restore on another account → delete everything | all items of §1 |
| L10-02 | Eval gate | Claude | all | signal oracle, routine eval (T49e), replay eval and executor eval green and recorded | record in the design notes |
| L10-03 | Pilot runbook | Claude | L10-01 | what a partner needs (Apple Silicon, macOS 14+, Gmail, optionally Slack with the right to create an internal app, Claude Code or Codex recommended, a Claude or ChatGPT account), adding Google test users, privacy explanation, EULA, weekly metrics export | Muse fact-check |
| L10-04 | Legal drafts | Claude → owner → lawyer | L7-09 | EULA, privacy policy, AI-provider notice drafted from the real behaviour of the code | lawyer reviews |
| L10-05 | Release `1.0.0-pilot` | Claude | all | DMG, SHA-256, notes, feed | installed on a clean Mac |

## 8. What we reuse from the existing code

| Area | Stays | Changes | Deleted (F9) |
|---|---|---|---|
| Python core | clean, verify, policy, resolve, memory, signals, assessment, routines, replay, learning, evals, synthetic (21k lines) + 23k lines of tests | extract (provider), cards and brief (no model), needs (no embeddings), connectors, ingest, db, maintenance, tasks | crypto/KMS, storage/GCS, Vertex, budget, task HTTP endpoints |
| SQL | 30 migrations, 50 functions, pgTAP | + shim, + `device`/grants in the cloud | — |
| `apps/api` | MCP transport, auth middleware, PAT, rate limit, Origin checks → relay | + relay, + OAuth | read, setup, contributor, connect, webhooks, uploads, vault routes |
| Contracts | OpenAPI and schemas | + local routes, MCP tools, protocols | team routes |
| Mac | most screens, APIClient, SupabaseAuth (for the link), Notifier, Notch | data source, onboarding, AI tools, mail | Team, Contributors, ContributorHome, Vault* |
| Infra | Cloud Run `apps/api`, Secret Manager, WIF | + release bucket | worker, Tasks, Scheduler, Pub/Sub, KMS, data buckets |
| Slack | data model ready: `chat` records, channel → client (`connection_scope`), `slack_id` identities, upload in the connector plan | new: connector, cleaning, resolution, multi-channel signals, Northbeam Slack, Mac connect (no Slack connector existed) | — |

## 9. Owner tasks (everything else is ours)

| When | Task | Why |
|---|---|---|
| by day 6 | Google Cloud Console: create an **OAuth client of type "Desktop app"** and add test users (yourself, the test account). The lead sends exact steps. | F2 |
| by day 8 | Create a **test Google account** (for example `firra.test.northbeam@gmail.com`) and add it as a test user | L2-04 |
| by day 12 | **Claude Code** installed and signed in on the test Mac | L3, G-C |
| by day 12 | Create a free **test Slack workspace** ("Firra Test Northbeam") and give the lead access to create a test app from the manifest | L2S-14, G-S |
| by day 14 | **Buy a domain**; the lead sets DNS | relay, OAuth, Google consent, public pages |
| day 12 | Go for **one real executor eval run** (their subscription or up to 15 USD of API) | L3-06 |
| days 23–25 | **Interop clicks:** add the connector in claude.ai and ChatGPT, click "Connect" and "Allow" | L7-07 |
| day 27 | Approval to **delete staging data** and cloud resources | L9-02 |
| from day 28 | A lawyer reviews the EULA, privacy policy and AI notice | L10-04 |
| from day 28 | Find 2–3 pilot partners (Apple Silicon Mac, Gmail, a founder who talks to clients, ideally Slack with clients) | pilot |

## 10. Testing and evals

All local (`scripts/check.sh`), no Actions minutes. Layers: unit (pytest, vitest, Swift Testing); integration (engine on local
Postgres, HTTP tests of the read models, MCP contracts); pgTAP; oracles (independent tests written from the spec for 🔒
packages); evals (signal oracle, perfect-extraction, T49e routine eval, replay, executor eval on Northbeam); multi-channel
(Northbeam Slack scenarios); E2E (test Gmail + test Slack + real Claude Code + app + MCP stdio and link, manual protocol
L10-01); security (threat model, executor sandbox test, log hygiene, relay oracle); install (clean macOS user, upgrade,
uninstall). **Never real client data in development.**

## 11. Risks and fallbacks

| # | Risk | Known by | Fallback |
|---|---|---|---|
| R1 | Bundled Python and Postgres unstable without notarization (Gatekeeper, library paths) | G-A (day 3) | (a) copy the engine into Application Support at first start and clear quarantine; (b) otherwise ask the owner about an Apple Developer account |
| R2 | `claude -p` / `codex exec` cannot be run safely without tools, or the subscription terms forbid scripted use | G-A (L0-06) | in-conversation processing only (L3-04) + a clear message; tell the owner before building |
| R3 | Extraction quality on real mail (G1 skipped) | first pilot | test Gmail and executor eval reduce it; the first pilot session is joint |
| R4 | The Supabase OAuth server fails the criteria of #163 | day 13 | own authorization server (+3 days) |
| R5 | ChatGPT connectors need something we do not support | day 3 (L0-05) and day 24 | release with Claude, ChatGPT later |
| R6 | Gmail import does not keep original dates (test account) | day 9 | IMAP APPEND with date, or local mbox import for E2E (UNVERIFIED) |
| R7 | Login item does not work with an ad-hoc signature | day 16 | instructions: add to Login Items by hand |
| R8 | The user's subscription cannot take the backfill | E2E | 3 months first, pause until limits reset, visible progress |
| R9 | Relay with one instance has limited capacity | after the pilot | shared channel (Pub/Sub) before public launch |
| R10 | The lead is the bottleneck (review + 🔒 packages) | always | Muse Spark writes oracles ahead; Codex takes bigger packages; review the same day |
| R11 | Schedule overrun | weekly | cut first: ChatGPT interop, `.mcpb`, Codex executor, metrics, Slack export import. Slack does not block the pilot (G-S). Never cut: quote verification, sandbox, backup, deletion, security review, multi-channel signals (without them Slack causes false alarms). |
| R12 | Slack API terms forbid or restrict our use (storing messages, sending to an AI) | day 4 (L2S-01) | stop before building, tell the owner; fallback: export import the user makes themselves |
| R13 | A workspace admin must approve an internal Slack app and the user is not admin | L2S-01, pilot | instructions for the admin; fallback: export import |
| R14 | External users in Slack Connect have no visible email | L2S-01 | map by channel (channel → client), not by email |
| R15 | Slack messages are short and numerous and eat the user's AI subscription | E2E | packets per thread or channel day, 3 months default, mapped channels only |
| R16 | Prompt injection through raw source excerpts (Layer 2 puts third-party text into the user's AI) | S3o | `untrusted_text` wrapping, caps, stripped control characters, no side-effect tools except sync, writes need `user_confirmed`; switch off by default |
| R17 | Users expect inbox-wide or cross-client search through Firra | pilot | say clearly that Firra searches one client at a time; the AI's own Gmail/Slack connector remains the tool for the rest |

## 12. Not in v1 (on purpose)

Teams and shared memory · Outlook/Microsoft 365 · Windows, iOS · passthrough relay (before public launch, v1.1) · notarization and
automatic updates · billing · embeddings and semantic search · a model that writes card and brief text (the user's AI does that) · full
whole threads or attachments through MCP · live Gmail/Slack retrieval at question time · cross-client or inbox-wide source search · "always available" mode · local model · Intel Macs · a Firra-distributed Slack app (Marketplace), Slack Events API
and Socket Mode · Slack files, canvases, calls and huddles (audio never) · other sources (Teams, transcripts, PM tools, CRM).

## 13. After v1

1. Pilot with 2–3 partners, weekly metrics export, measure the exit of milestone E (≥ 50 % of *important* rated useful, notifications on
   for 2 weeks).
2. Passthrough relay (the relay sees only encrypted bytes). With D1 that means Firra's servers see nothing about clients.
3. Google verification (domain, privacy URL) + CASA if S1 says it is needed.
4. Apple Developer + notarization + automatic updates, when partners the owner does not know personally arrive.
5. Billing (Lemon Squeezy licenses).
6. Outlook.

## 14. Coverage check ("did we forget anything")

| Area | Covered in |
|---|---|
| Database, migrations, shim | L1-01, L1-02, L1-03, L1-09 |
| Job queue, scheduler, sleep and wake | L1-04, L1-05, L2-05 |
| Secrets, Keychain, encryption, FileVault | L1-06, L1-07, L5-01 |
| Google OAuth, sync, calendar, file import | L2-01, L2-02, L2-06 |
| Slack: terms, connect, sync, cleaning, minimization, mapping, chat extraction, multi-channel signals, Northbeam Slack, export, Mac, E2E | L2S-01..L2S-14 |
| Onboarding, client selection | L2-03, L5-03 |
| AI extraction (CLI, conversation), sandbox, instructions, eval, limits | L3-01..L3-08 |
| Cards, briefs, needs without a model | L3-09, L3-10 |
| Read API, writes, audit | L4-01, L4-02a–d |
| MCP tools, prompts, skill, answer test | L4-03..L4-06 |
| Client-scoped source layer (search, excerpts, refresh, sync status, switch, scopes) and data at rest | F4b S0..S8 |
| Mac: engine, onboarding, statuses, notifications, AI tools, mail | L5-01..L5-08 |
| Packaging, signing, DMG, feed, upgrade, uninstall | L0-07, L6-01..L6-04 |
| Relay, OAuth, devices, interop, public pages | L7-01..L7-09 |
| Retention, backup, deletion, diagnostics, metrics, logs | L8-01..L8-06 |
| Security review | L8-07 |
| Code, infra and documentation cleanup | L9-01..L9-03 |
| E2E, evals, pilot runbook, legal drafts, release | L10-01..L10-05 |
| Project rules and workflow | L0-01, L0-02 |
| Legal and Google requirements | L0-04, L7-09, L10-04 |
| Owner tasks | §9 |

## 15. Findings from the first research and spikes (2026-10-05)

| Item | Finding | Effect on the plan |
|---|---|---|
| **S2 packaging** (`docs/research/engine-bundle.md`) | Postgres 17.11 + pgvector 0.8.2 built from source, relocatable (71 Mach-O files, all arm64, system or `@rpath` libraries only); Python 3.12.14 standalone with the worker's wheels. Whole bundle 135 MB, DMG 74 MB; initdb 0.5–0.9 s, Postgres start 0.2 s, Python first query 0.7 s; no TCP listener. **A re-signed, quarantined copy stalls under macOS launch assessment; a copy with quarantine cleared works with no admin rights.** | **R1 fallback (a) accepted:** the app copies the engine into Application Support, clears quarantine on the copy and starts that. Clean-account and macOS 14/15 runs are still unverified (L6-04). Developers test on a **copy** of the bundle, never on a freshly re-signed build directory. |
| **S1 Google** (`docs/research/google-local.md`) | `gmail.readonly` is restricted; `calendar.readonly` is sensitive. CASA is required when an app "has the ability to access data from or through a third-party server". An older Google FAQ exempted local client applications (and user-configured transmissions off the device); the current FAQ no longer shows that text, and community answers say CASA may still apply. Unsettled: derived facts to our relay, and text sent to the user's own AI. | The pilot is unaffected (Google test users). **Before public launch ask Google through the verification form** with our exact data flow. Budget for CASA until Google says otherwise. |
| **S3a AI CLI** (`docs/research/ai-cli.md`) | `claude -p` has `--tools ""`, `--strict-mcp-config`, `--restricted`, `--bare`, `--json-schema`, `--no-session-persistence`; `codex exec` has `-s read-only`, `--ignore-user-config`, `--ephemeral`, `--output-schema`. Both can report login state without a prompt. **Terms:** Anthropic's Consumer Terms forbid access "through automated or non-human means, whether through a bot, script, or otherwise" except via an API key "or where we otherwise explicitly permit it" (read by the lead on the live page, 2026-10-05). Secondary sources say running the official Claude Code binary on the user's own machine with their own login is permitted and that `claude -p` still draws from subscription limits; no primary statement found. | **R2 stays open.** The design already has two fallbacks: in-conversation processing (L3-04) and a user-supplied API key (decision D1-B in the notes). Owner task: ask Anthropic and OpenAI support in writing whether an app that launches the user's own signed-in CLI in the background on the user's machine is permitted. Until answered, onboarding presents background AI processing as **the user's choice**, with the in-conversation route equally easy. L3-08 must still prove the sandbox flags. |
| **S4a MCP clients** (`docs/research/mcp-clients.md`) | claude.ai (web, mobile, desktop) connect to remote MCP **from Anthropic's cloud**, so the relay must be reachable publicly (as planned). DCR and CIMD are both supported; redirect `https://claude.ai/api/mcp/auth_callback`; Streamable HTTP. **ChatGPT custom connectors need a Business/Enterprise/Edu plan with developer mode enabled by an admin (Plus/Pro unverified), and OAuth with refresh tokens and `offline_access`.** Cursor and VS Code have install deeplinks; Codex has `codex mcp add`. | R5 refined: ChatGPT is a **later** target for solo founders on consumer plans; claude.ai, Claude Desktop, Claude Code, Codex, Cursor and VS Code are the v1 set. Build OAuth with DCR + CIMD, refresh tokens and `offline_access` from the start. Offline behaviour of each client is untested (S4 interop). |
| **L2S-01 Slack** (`docs/research/slack-local.md`) | A user-created **internal** app keeps Tier 3 limits (50+ requests/min, 1,000 objects); apps distributed outside the Marketplace are cut to 1 request/min and 15 objects. Any member may create and install an internal app unless the workspace turns on app approval. A manifest link `https://api.slack.com/apps?new_app=1&manifest_json=…` works. Slack's ToS say the Marketplace is the only appropriate channel for "commercially distributing" apps, including "templatized one-off custom" apps; sharing a manifest template could be read that way. API terms ban LLM training on API data and bulk export **by providers of apps used outside the organisation**. | T22 holds and is load-bearing: **never ship a Firra-owned Slack app id.** Give the manifest as text the user pastes (the link is a convenience). **Lawyer pass on the Slack ToS before F2b build** (owner task, with the other legal drafts). Free-workspace rights unverified: check in L2S-14. |
