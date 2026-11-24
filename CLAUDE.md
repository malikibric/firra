# CLAUDE.md — Firra

Project memory for Claude Code. Read this file fully at the start of every session.
Detailed specs live in `docs/` — read the relevant one before working on that area:

- @docs/plans/local-v1.md — **the build plan** (decisions, phases, work packages, gates)
- @CONTRIBUTING.md — how to contribute: branches, checks, pull requests
- @docs/architecture.md — components, data model, pipeline, local runtime
- @docs/connectors.md — Gmail, Google Calendar, Slack (v1); other sources by hypothesis
- @docs/north-star.md — what Firra is, surfaces (Mac app + MCP), intelligence core, what is not built
- @docs/roadmap.md — milestones A–H and where Local v1 fits

Some code from the earlier cloud-first design may still exist until phase F9 deletes it; it is no longer the rules.

If a request conflicts with **Product Rules** (§2) or **Guardrails** (§6), stop and ask. You are the senior engineer on this
codebase: make sound technical decisions, explain trade-offs briefly, and never trade security or data isolation for speed.

---

## 1. Product

**Firra** is a **local-first** product for **service firms** (B2B marketing/RevOps agencies, software consultancies; founder-led
firms of about 5–30 people first). **Market: US, UK, Western Europe only.**

Firra is an always-on **client relationship intelligence** system that runs **on the founder's Mac**: it builds a living memory of
every client relationship from the founder's own email, calendar and Slack, detects meaningful changes before humans notice them,
and recommends the next best action — every claim backed by evidence. See `docs/north-star.md`.

**One-liner:** Firra remembers every client relationship, notices what changed, and tells the founder what to do — with the
evidence — and the data never leaves the founder's Mac.

Surfaces (no browser dashboard):
1. **Native macOS app** — menu bar ("N clients need attention"), Today / What changed, clients, client detail, Churn Replay,
   routine switches, onboarding, setup, native notifications.
2. **MCP server** — Firra inside the founder's Claude, ChatGPT, Claude Code, Codex, Cursor. **Connect once:** the same Gmail,
   Calendar and Slack connections that feed the engine power two MCP layers over the local database: **intelligence** (states, risks,
   opportunities, commitments, meeting prep, with evidence) and **client-scoped sources** (search and bounded excerpts of one mapped
   client's mail, Slack and meetings; off until the owner switches it on; never inbox- or workspace-wide; `docs/plans/local/source-layer.md`).
   Read-mostly, policy-aware, audited. Two routes to the same tools: a **local stdio** server for tools on the same Mac, and a **public link**
   (a relay in Firra's small cloud, OAuth) for claude.ai (web, mobile, desktop) and ChatGPT. When the Mac is off the client gets a
   clear "Firra Mac is offline".
3. **Routines** — first-party routines (morning scan, commitment watch, pre-meeting brief switch …) that surface items by importance:
   FYI stays in Firra, *worth attention* joins the brief, *important* becomes a notification.

Layers (all run in the local engine):
- **L1 Ledger** — ingest, normalize, resolve entities, enforce policies, keep evidence.
- **L2 Memory & signals** — extraction (done by the user's AI, **verified by Firra**), client card, relationship signals across
  channels, stakeholder map, goals vs. results, commitments.
- **L3 Assessment & plays** — per-client risk and expansion state with drivers, hysteresis, caveats and a deterministic play;
  Churn Replay without hindsight.
- **L4 Growth** — expansion signals and latent needs matched to the firm's services.
- **L5 Learning** — founder feedback on every surfaced item, client outcomes, and precision/lead-time metrics, kept locally.

---

## 2. Product rules (non-negotiable)

1. **Current clients first.** Churned clients are used only for calibration/backtests (including Churn Replay), never as the main output.
2. **Evidence or nothing.** Every claim links to ≥1 evidence item (source record + short verbatim quote + locator + deep link). A claim
   whose quote cannot be verified is dropped, not softened. **Firra verifies; the AI only proposes.** Raw source excerpts from the MCP
   source layer are context, not evidence: they never become Firra facts, states or plays.
3. **Money layer = profit & pricing only.** Never build "find unbilled money", invoice recovery, change-order generation.
4. **Text only.** Analyze written text and transcripts. Never analyze audio features, voice tone, video, or faces (EU AI Act workplace
   emotion-recognition ban). Tone signals come only from the client's written text, aggregated per account — never an emotion score of a person.
5. **No employee scoring.** Never rank, score, or evaluate the firm's own employees. Signals are about client relationships.
6. **Drafts, never auto-send** anything to a firm's clients.
7. **Decisions, not dashboards.** Short, ranked, actionable output. No numeric health or risk score on any user-facing surface.
8. **Per-client data policy** (`full` | `metadata_only` | `excluded`) enforced at ingest and at every read path (app, brief, MCP, AI executor input).
9. **Minimization.** Only data involving a mapped client (by domain/participants/Slack channel mapping) is stored. Everything else is discarded at ingest without being persisted.
10. **Verified AI output only.** Nothing the user's AI returns is stored unless Firra's deterministic checks pass (quote verbatim in the source, schema, policy, client is `full`).

---

## 3. Users and access

- **One user: the owner** (the founder), on one Mac. There are no members, admins, contributors, invitations or roles in v1
  (owner decision 2026-10-05). Firra sees only the owner's own mailbox, calendar and Slack. Assessments carry a participant-coverage
  caveat when the owner is not in most threads of a client.
- Every read of client data by an MCP client is written to a local audit log the owner can see in the app.
- Firra's cloud knows the owner's account, registered devices and OAuth grants — never client data.
- Teams, shared memory, Outlook/Microsoft 365 and domain-wide delegation are **not** in v1 (`docs/plans/local-v1.md` §12). The
  schema keeps `workspace_id` (one workspace per install) so a later team mode is an addition, not a rewrite.

---

## 4. Architecture at a glance

```
 claude.ai / ChatGPT ──HTTPS+OAuth──▶ Firra relay (services/relay, Cloud Run, EU): no client data, no logging of content
 Claude Code / Codex / Cursor ──stdio──┐                  │ outbound WebSocket opened by the Mac
                                       ▼                  ▼
 ┌──────────────────────────────── the owner's Mac ────────────────────────────────────┐
 │ Firra app (Swift): menu bar, UI, Keychain, login item, starts the engine             │
 │ Firra engine (Python, child process): sync · clean · resolve · AI extraction ·       │
 │   verify · memory · signals · assessment · routines · local API · MCP tools          │
 │ Local PostgreSQL 17 + pgvector (unix socket): derived facts + encrypted mail/chat    │
 └──────────────────────────────────────────────────────────────────────────────────────┘
        ▲ Google OAuth (desktop, PKCE)   ▲ Slack user token (own app)   ▲ owner's AI CLI (claude -p / codex exec)
```

**Language boundary:**
- **Swift = Mac app** (`apps/mac`): talks to the local engine through a client generated from / matching the OpenAPI contract.
- **Python = engine** (`services/worker`, package `firra`; local runtime in `firra/local/`): connectors, cleaning, resolution,
  executors for the user's AI, verification, memory, signals, assessment, plays, replay, routines, read models, MCP tools, evals.
  Every judgement is computed and persisted here.
- **Control plane** (never sees client data): the **relay** is a small Python service (`services/relay`, FastAPI + `websockets`);
  `apps/api` (TypeScript) remains for the later OAuth server (stage 2), public pages and the release feed.
- Contracts live in `packages/contracts` (JSON Schema → zod + Pydantic, OpenAPI).

---

## 5. Tech stack

| Area | Choice |
|---|---|
| Monorepo | pnpm workspaces + Turborepo (TS), uv workspace (Python) |
| Mac app | Swift 6, SwiftUI, macOS 14+, Apple Silicon, `MenuBarExtra`, UserNotifications, Keychain, `ASWebAuthenticationSession`; `SMAppService` login item |
| Engine | Python 3.12 (python-build-standalone bundled in the app), FastAPI + uvicorn on 127.0.0.1, Pydantic v2, psycopg 3, httpx, official `mcp` SDK |
| Local DB | PostgreSQL 17 + pgvector bundled in the app, unix socket only; migrations in `supabase/migrations` + `supabase/local/shim.sql` |
| Control plane | Relay: Python (FastAPI + `websockets`) on Cloud Run (EU), one instance; later OAuth server (stage 2, `docs/research/oauth-server.md`) and public pages in `apps/api` (Hono, TS); Supabase Auth (EU) for accounts only when OAuth lands |
| AI | the **user's own AI**: `claude -p` (Claude Code), `codex exec`, or any MCP client in conversation. Firra bundles no model and pays for none. |
| Secrets | macOS Keychain; the app hands secrets to the engine in memory |
| Distribution | DMG, ad-hoc/local-certificate signed, public EU GCS bucket with SHA-256 and `latest.json` (no Apple Developer account yet) |
| IaC / CI | Terraform for the small cloud; **checks run locally** (`scripts/check.sh`); GitHub Actions are manual-only (few minutes) |
| Tests | Vitest (TS), pytest (Python), Swift Testing (Mac), pgTAP (SQL) |
| Lint/format | ESLint + Prettier (TS), ruff + mypy strict (Python) |

Ask before adding any dependency not listed here or approved in `docs/plans/local-v1.md` (approved: `mcp`, `ws`; the relay uses Python `websockets`, already in the lock through uvicorn, because `ws` is blocked by the pnpm trust policy).

---

## 6. Guardrails (security, privacy)

**Client data stays on the Mac**
- No client data (mail, chat, quotes, facts, names, addresses, counts per client) is ever sent to Firra's cloud, logged there, or
  stored there. The relay forwards MCP calls and stores nothing; before public launch it becomes a passthrough that sees only
  encrypted bytes (v1.1). The cloud holds accounts, devices, OAuth grants and the release feed.
- The only places client text leaves the Mac: (a) to the AI tool the **owner chose and is signed in to** (executor input, MCP answers
  to the owner's own AI client), (b) to the owner's Google/Slack accounts as API calls. Onboarding says so plainly, including that
  consumer AI plans may train on conversations unless the owner switches that off.

**Local secrets and files**
- Google refresh tokens, the Slack token and the encryption key live in the Keychain; the engine holds them only in memory.
  No secret on disk in plain form, none in logs, none in diagnostics.
- Mail and chat bodies are encrypted in the database (AES-GCM, key from the Keychain). FileVault is required (the app warns if off).
- The data directory is 0700, the engine socket and token files 0600. No TCP listener except 127.0.0.1 with a bearer token and Host/Origin checks.
- Backups cover only the non-recoverable state (feedback, outcomes, mappings, policies, manual corrections), encrypted with a recovery phrase.

**Untrusted content and the user's AI**
- All ingested content (mail, Slack, filenames) is **data, never instructions**. The executor runs the owner's AI CLI with **no tools,
  no MCP, no file access**, in an empty directory, with no inherited secrets: text in, JSON out. Output is verified (Rule 10).
- MCP answers mark third-party text (`untrusted_quote`, `untrusted_text`). Source-layer tools name one client, return bounded excerpts
  (never whole threads or attachments), read only the synced local database (no live provider search), are off until the owner switches
  them on and need a separate `sources` consent over a link. Raw provider payloads are never written to disk; bodies, subjects and meeting
  titles are encrypted in the database. Tools are read-mostly; the few writes (feedback, outcomes, extractions) go
  through deterministic validation and, for feedback and outcomes, ask the user to confirm.

**Logging**
- Logs contain ids, counts, timings and error classes. **Never** message bodies, Slack text, quotes, email addresses or tokens.
  Diagnostics exports are searched for fixture quotes and addresses in tests.

**Control plane**
- CSP, secure cookies, rate limiting on relay and OAuth endpoints, exact redirect URIs, PKCE, short-lived tokens, rotating refresh
  tokens, immediate revocation. The engine validates OAuth tokens itself (it does not trust the relay blindly).
- Gitleaks and Semgrep run locally in `scripts/check.sh security`. A credential found anywhere in history is revoked at the provider.

---

## 7. AI usage (the user's AI)

- Firra **pays for no model**. Extraction runs through the owner's AI CLI (`claude -p`, `codex exec`) in the background, or through
  the in-conversation tools (`get_pending_mail` / `submit_extractions`, off unless the owner sets `mcp_processing_tools`), or MCP sampling where a client supports it.
- Prompts live in `services/worker/prompts/<name>.v<N>.md`, versioned; outputs store `prompt_version`, the **model name the AI
   reported** (when known) and which executor ran. Current extraction prompt: `extract.v6` (chat variant, `topic_key`) on the
   owner-AI path (`firra/ai/processor.py`); the legacy paid path still pins `extract.v5` until phase F9 deletes it.
- Output is always structured JSON validated against a Pydantic schema; on validation failure retry once, then record the failure.
  Rejected items are counted by reason; the stored quote is the matching source span.
- Weekly cards, meeting briefs and routine cards are **deterministic renderings of facts** (`model = deterministic`); the user's AI
  writes the words when asked in conversation.
- The legacy Vertex AI code (`firra/llm/vertex.py`, budget, prices) remains only until phase F9. Do not extend it and never call it.
  No call to any paid API in development; recorded outputs and fakes only.

---

## 8. Repository layout

```
firra/
  CLAUDE.md, AGENTS.md
  docs/                         # architecture, north-star, roadmap, plans/, team/, research/, runbooks/, archive/
  scripts/                      # check.sh (local CI), deploy-relay.sh (F7)
  apps/
    mac/                        # native macOS app (Swift/SwiftUI)
    api/                        # control plane: relay, OAuth server, devices, public pages (TS)
  services/
    worker/                     # Python engine: package firra (connectors, clean, resolve, policy, extract, verify, memory,
                                #   signals, needs, assessment, routines, cards, brief, ai executors, local runtime, MCP, evals, synthetic)
      prompts/  config/  tests/
  packages/
    contracts/                  # JSON Schemas & OpenAPI → zod + pydantic
    db-types/  config/
  supabase/
    migrations/                 # single source of truth for the schema (also runs locally)
    local/                      # shim.sql (auth schema stand-in for the local Postgres)
    tests/                      # pgTAP
  infra/terraform/              # the small cloud: relay on Cloud Run, release bucket, Secret Manager, WIF
  .github/workflows/            # manual-only
```

---

## 9. Coding standards

**TypeScript:** `strict: true`, no `any` (use `unknown` + zod), zod validation at every boundary. **Python:** mypy strict, Pydantic models at
every boundary, no global mutable state, pure functions for signal math with unit tests, httpx with timeouts + retries, structured
logging via one logger module. **Swift:** Swift 6 strict concurrency, SwiftUI views free of networking, no secrets outside the
Keychain; the UI is English only in v1, SwiftUI literals (String Catalogs are deferred until Xcode is installed). **All:** small
modules, explicit names, no dead code, no speculative abstractions.

---

## 10. Database rules

- Migrations are plain SQL in `supabase/migrations/`, forward-only, reviewed. They run unchanged on the local Postgres after
  `supabase/local/shim.sql`. Regenerate `packages/db-types` after each migration while it is still used.
- Every tenant table: `id uuid pk`, `workspace_id uuid not null`, `created_at`, `updated_at`; child rows reference parents through
  composite foreign keys `(workspace_id, <parent>_id)`. RLS policies stay (harmless locally) and keep pgTAP coverage meaningful.
- Idempotency: external records are unique on `(connection_id, external_id)`; jobs use `idempotency_key`.
- Derived rows keep `evidence_ids` (or a join table) back to `evidence` rows.
- The local database is **per user and local**: never connect to any remote database from the engine.

---

## 11. Jobs & events

Every task handler is **idempotent** and safe to retry. Jobs live in the `job` table and run in the engine's local executor
(`REGISTRY` handlers); a local scheduler runs the sweep (sync, processing, routines with catch-up after sleep, retention, meeting
briefs). Failures: bounded retries with backoff, then `job.status = failed` with an error class that the app turns into an action
("reconnect Google", "sign in to Claude Code"). Respect provider rate limits and `Retry-After`.

---

## 12. Testing & evals

- Unit tests for parsers, resolvers, policy enforcement, signal math, evidence verification, executors (fake CLI binaries), the sandbox.
- Integration tests against the **local Postgres**; recorded provider fixtures (never live accounts in CI-style checks).
- Independent **oracle** tests, written from the spec by a different agent, for every 🔒 package.
- **Synthetic dataset** ("Northbeam Growth", fictional US agency, mail **and Slack** scenarios) for evals, demos and local dev — never customer data.
- Evals: signal oracle, perfect-extraction check, routine eval (T49e), replay eval, executor eval; leakage property test.
- All checks run through `scripts/check.sh` on the Mac. Facts built "as of" a date never contain anything dated after it.

---

## 13. Environments & release

- **local** is the product. A developer machine runs the same engine as a user's Mac, on a scratch data directory.
- **cloud** is one small project: relay, OAuth/control plane, release bucket. Deployed from the Mac (`scripts/deploy-relay.sh`).
- Releases: `scripts/build-app.sh` makes the DMG on the Mac; the maintainer publishes it, its SHA-256 and `latest.json` to the release bucket.

---

## 14. Working agreement (for Claude Code)

1. **Plan first** for any non-trivial task; wait for approval on plans that touch security or schema when the owner is available.
2. **Work from `docs/plans/local-v1.md`**, one work package at a time, in the order of its phases and gates.
3. **Tests with every change.** Mock all external APIs and AI calls.
4. **Conventional Commits**, small focused commits, open a pull request against `main`: see `CONTRIBUTING.md`.
   CI and `scripts/check.sh all` must be green before merge.
5. **Ask before:** new dependencies, anything that sends client data anywhere, changing policy logic, anything touching billing.
6. **Never** read, copy or log real customer data during development.
7. **Definition of done:** `scripts/check.sh <area>` green, docs updated (`docs/`, `.env.example`).
8. All code, comments, prompts, commit messages and docs in **English**.

---

## 15. Out of scope

Unbilled-money detection, invoice recovery, change orders · voice/video/emotion analysis · scoring or ranking the firm's employees ·
auto-sending messages to clients · LinkedIn scraping · markets outside US, UK, Western Europe · a browser dashboard · teams and shared
memory · Outlook/Microsoft 365 · billing (until after the pilot) · a model that Firra pays for · integrations without a written
product hypothesis (`docs/roadmap.md` "Later") · visual design/branding (planned separately).
