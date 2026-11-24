# Firra — Roadmap (v2, risk-driven)

Adopted 2026-09-30 with `docs/north-star.md`. The v1 feature roadmap (P0–P10) is archived in
the earlier design (not included); how its items moved is in the design notes.

Each milestone proves a product hypothesis. It is done when its **exit criteria** pass — measured by
evals or by real founders — not when its features exist. Work in order; a later milestone may start
early only where this file says so. Task-level work: the original task spec, live status:
the original task spec.

**Already built (v1 P0–P2, accepted or merged):** monorepo, auth, workspaces, roles, invitations,
contributor connect, audit, Gmail/Google Calendar (user + domain-wide delegation), Outlook code
(untested), mbox/eml/pst upload, 12-month backfill + incremental sync, clients/domains/policies,
minimization, 15-day body retention, service catalog, cleaning, verified extraction (`extract.v1`,
paid switch off), client memory (card, stakeholders, champion, commitments, goals, unknowns),
14 signal rules, latent needs (F2), policy → derived data, P2b schema (signal history, assessments,
replay runs, observations, feedback, outcomes), Northbeam v3 synthetic spec with dated expectations,
eval scorer. (Cloud v1 history: teams/roles/invitations/contributor connect are removed in Local v1 —
one owner, no teams; the 15-day server retention is replaced by bodies encrypted on the Mac, 12 months
by default. The old `apps/api` read/setup/connect routes still exist; phase F9 deletes them.)

**Paid AI rule (Local v1):** Firra pays for no model. Real extraction runs go through the **owner's own AI subscription** (or an API key the owner
chooses); a real executor eval run needs the owner's explicit go. Development uses recorded outputs and fakes only. The earlier rule (Vertex runs capped at
15 USD in staging, O-D4) applies to the legacy code only until phase F9 deletes it.

---

## Local v1 — re-platforming (adopted 2026-10-05)

Owner decision: Firra runs entirely on the owner's Mac (one user, no teams); extraction is done by the owner's own AI and verified by Firra; MCP works locally
and through a public link for Claude and ChatGPT; Slack joins Gmail and Calendar as a source. The build plan with phases F0–F10, gates G-A…G-E, G-S and work
packages is `docs/plans/local-v1.md`; this table says how the milestones below map onto it.

| Milestone | State | In Local v1 |
|---|---|---|
| A Intelligence, A2 | built and evaluated on synthetic data | kept; extraction provider changes (F3), multi-channel signals added (F2b) |
| B Churn Replay | built | kept |
| C Closed loop, API | built | read models and writes port to the local engine (F4); contracts stay |
| D Mac app | built against the cloud API | rebuilt on the local engine (F5); team screens removed |
| E Routines | built, eval green; partner exit pending | kept; runs in the local scheduler (F1); exit measured through local metrics export |
| F MCP | read-only PAT preview built | local stdio + relay with OAuth (F4, F7); client matrix from research S4a |
| G Delivery | parked | unchanged |
| H Release hardening | gap analysis done | waivers below; local equivalents: backup of non-recoverable state, deletion, security review, clean-install protocol |

**Pilot waivers (owner, 2026-10-05):** free pilot; no billing, plan limits, data export, load test, independent security review, CASA (until S1 says otherwise),
Microsoft verification. **Not waived:** verified deletion, backup and restore drill, signed agreement and privacy/terms (a lawyer reviews the drafts of L10-04).

---

## A — Intelligence foundation

**Hypothesis:** verified extraction + temporal memory can produce client states and plays that match
what an attentive founder would conclude, early, without false alarms.

- **User-visible outcome:** none directly; a live `account_assessment` per client and day (risk,
  expansion, drivers, strength, caveats, play) that any interface can show.
- **Backend:** extraction v2 (budget direction, initiatives); facts as of any date (no leakage);
  signal families, watch bands, silent-churn rules, coverage guard; signal history
  (`signal_event`); assessment engine with hysteresis; expansion state; plays (`plays.yaml`);
  unresolved complaints; deterministic fact rendering for reasons; `cards.v1` wording with
  number/date/fact-id checks; eval run orchestration.
- **Evals (Northbeam v3; Local v1: recorded outputs and fakes, a real executor run only with the owner's go on their own subscription; originally real models on staging, ≤ 15 USD):** citation validity 100 %;
  signal recall ≥ 0.8 incl. new rules; risk-state accuracy at as-of ≥ 0.8; at least 2 of 3 losses
  detected ≥ 30 days ahead; `umber` ≥ `elevated` and `vantage` by their dates; benign controls
  (`pinecrest`, `halden`) never `elevated`; false alarms ≤ 1 client-week per 10 clients per month;
  flicker 0; play accuracy ≥ 0.8; 0 numeric-integrity failures in cards; cost per run reported.
  These are the **risk-side gates**. The growth-side gates (latent needs found with exact counts,
  no prohibited needs, expansion-state accuracy ≥ 0.8) moved to **A2** on 2026-10-01: the first real
  runs showed they depend on how well a small model recognises statements of need, which is a
  separate piece of work (see below). **Free CI checks:** oracle cases for every rule, perfect-extraction check (rules +
  weekly assessments on the planner's facts meet every expectation), leakage property test, pgTAP.
- **Dependencies:** none (in progress).
- **Exit:** all risk-side gates above pass in one recorded run (the design notes). **Reached on 2026-10-01 (run 5).**

### A2 — Growth signals on real extraction (follow-up to A)

Latent needs, expansion states and the `expansion_conversation` play on real model output. State
after three real runs: the pipeline works end to end; extraction noise is down (`extract.v3`); a
need for a service the client already has no longer raises an expansion; a model-facing `need`
item kind (extract.v4, stored as a `request` with `form: need`, no migration) raised recall of the
planted requests from 8-9 to 11 of 12 in the lab at the same price. Open: the planted needs sit
exactly on the 3-mention threshold, so one missed mention still changes the exact counts.
**Gates:** planted needs found (mention and thread counts within 1 of the plan, owner decision 2026-10-01), no prohibited needs, expansion-state accuracy ≥ 0.8.

**Decision 2026-10-03 (lead, owner delegated): A2 is parked, not a blocker.** The local run of the same
day (the design notes) passed the expansion-state gate (11/12, was 5/12) and the prohibited-needs
gate (0); only the exact latent-need counts still fail (1/2 planted needs within ±1). Founders see growth
signals as states with evidence, not as counts, so the remaining gap changes no screen. It is reopened
when a design partner's mail shows a missed need, or before the next prompt change, with a fresh spend go.
The `kestrel` service question stays in the truth file until then.

## B — Churn Replay (trust wedge)

**Hypothesis:** shown on their own history, a founder recognises that Firra would have warned them
early enough to act.

- **User-visible outcome:** a Replay report per client: state timeline week by week, the first
  week at `watch` / `elevated`, lead time before notice/churn, the signal families and exact
  messages/events behind each transition, the play Firra would have recommended, and — for
  retained clients — false alarms shown next to hits. Before the Mac app exists it is rendered by an
  operator CLI (markdown/HTML) and walked through with the founder.
- **Backend:** replay runner (weekly steps, no hindsight, idempotent), replay summary, transition
  narrative built deterministically from replay rows, report renderer, `replay` read model.
- **Evals:** Northbeam: ≥ 2 of 3 churned clients detected ≥ 30 days before churn; identical
  summary on re-run; leakage test green; every claim in the report links to evidence or a metadata
  input. **Founder test:** ≥ 2 design-partner sessions on their own mail (Google test users, DPA
  signed); in each, the founder marks at least one finding "did not know" or "would have noticed too
  late", and no finding "wrong" goes unexplained.
- **Dependencies:** A (assessment engine). Real-data sessions need a design partner, signed DPA and
  the owner's go for their backfill extraction cost (estimated per mailbox before running).
- **Exit:** both eval sets pass; session notes in the design notes.

## C — Closed loop and the Firra API

**Hypothesis:** founders will tell us what was useful, and outcomes can be captured, so Firra can
measure and improve its own precision.

- **User-visible outcome:** every surfaced item (state change, card, replay finding) can be marked
  useful / already knew / wrong / not now, with the action taken; outcomes (renewed, churned,
  expanded, downgrade, unchanged) are recorded per client.
- **Backend (Local v1: the same read models and writes now live in the engine on the Mac, F4; `apps/api` keeps only relay and OAuth):** Firra API `/v1` in `apps/api`: auth (Supabase session tokens), workspace
  authorization, RLS-scoped read models (clients needing attention, client state and change,
  client detail, timeline, evidence, replay), feedback and outcome writes through the existing
  audited RPCs, audit on reads, rate limits, OpenAPI from `packages/contracts`; surfaced items
  unified as decision cards with kinds; learning metrics (precision, lead time, usefulness per
  signal family and play) computed by the worker.
- **Evals:** contract tests for every endpoint; tenant-isolation and policy tests (no `excluded`
  data, no quotes for `metadata_only`); feedback → metric pipeline verified on synthetic feedback;
  learning metrics recomputed independently in tests.
- **Dependencies:** A; B for the replay read model.
- **Exit:** the Mac app and MCP can be built against the API without new backend logic.

## D — Minimum native Mac product

**Hypothesis:** a menu-bar-first Mac app makes Firra part of the founder's day without opening a
dashboard.

- **User-visible outcome:** sign in; menu bar with "N clients need attention" and the top items;
  Today / What changed; client list; client detail (state, what changed, drivers with evidence,
  stakeholders, champion, commitments, goals, unknowns, needs, play, history, outcomes); Churn
  Replay; feedback controls; setup (clients and policies, sources and their sync state; Local v1: no members or contributors); native notification plumbing (no routines yet).
- **Mac:** Swift 6 + SwiftUI, `MenuBarExtra`, `UserNotifications`, Keychain, sign-in through
  `ASWebAuthenticationSession` (Supabase Auth, PKCE, `firra://` callback), generated OpenAPI client,
  macOS 14+, signed with a local certificate and shipped as a DMG (no Apple Developer account, no
  notarization, no auto-update for now: `docs/plans/d-mac-app.md`). English first with String Catalogs.
- **Backend:** only API additions the screens prove necessary; setup endpoints over existing RPCs.
- **Evals:** a founder answers "who needs attention, what changed, why, what to do, what evidence"
  within 2 minutes of opening; UI tests for sign-in/expiry/offline; no screen shows `excluded` data
  or `metadata_only` quotes; the DMG installs on a clean Mac (right-click Open).
- **Dependencies:** C. Owner: design partners (Xcode and the Apple Developer Program are not needed).
- **Exit:** ≥ 2 design partners use the app for 2 weeks (the web app is already removed).

## E — Routines

**Hypothesis:** a few opinionated routines deliver the right item at the right time without spam.

- **User-visible outcome:** first-party routines — Morning client scan, Pre-meeting brief,
  Commitment watch; then Weekly founder review, Expansion scout, Risk watch. Each with an
  importance threshold: FYI stays in Firra, *worth attention* joins the brief, *important* becomes a
  native notification.
- **Backend:** routine definition + run model (designed so natural-language routines fit later);
  routine engine reads assessments, changes, commitments and feedback — never a raw
  prompt over mail; pre-meeting brief (F1 inputs from memory, `meeting.v1` wording, verify);
  importance scoring rules + per-workspace notification budget.
- **Evals:** Northbeam: morning scan lists exactly the clients whose state changed or crossed a
  threshold; ≤ 3 *important* notifications per workspace per week; pre-meeting briefs 100 %
  citation validity (T45 evals, not the routine eval); feedback-aware suppression (an item marked "not now" is not re-raised without
  new evidence).
- **Dependencies:** D (notifications), C.
- **Exit:** design partners keep notifications on for 2 weeks; ≥ 50 % of *important* items rated
  useful.

## F — Remote MCP

> **Local v1:** the MCP tools live in the engine on the Mac (Python `mcp` SDK; stdio entry and a Streamable HTTP handler); `apps/api` becomes the relay and OAuth server for claude.ai and ChatGPT. Details: `docs/plans/local-v1.md` F4 and F7.

**Hypothesis:** founders who live in Claude/ChatGPT get Firra's intelligence there without Firra
becoming a wrapper.

- **User-visible outcome:** "Which clients need my attention?", "Prepare me for Acme", "Why is Acme
  on watch?", "What do we still owe Delta?" answered from Firra data with evidence links.
- **Backend:** MCP tools live in the engine (`services/worker/firra/local/mcp/server.py`, 22 tools by default (24 with `mcp_processing_tools`) in two layers:
  intelligence `get_clients_needing_attention`, `list_clients`, `get_recent_changes`, `get_client_state`,
  `get_client_context`, `get_risks`, `get_commitments`, `get_stakeholders`, `get_client_unknowns`,
  `get_client_timeline`, `search_evidence`, `get_evidence`, `get_churn_replay`, `get_decisions`,
  `prepare_for_client`, `get_opportunities`, plus `record_feedback` / `record_outcome` (validated writes);
  client-scoped sources `search_client_sources`, `get_source_context`, `refresh_client_sources`,
  `get_sync_status`; and, only when the owner switches `mcp_processing_tools` on, the in-conversation extraction pair `get_pending_mail` / `submit_extractions`),
  served over local stdio (`firra-mcp`) and Streamable HTTP; the relay for claude.ai and ChatGPT is
  `services/relay` (Python, connector links in stage 1, OAuth in stage 2). Read-mostly (not read-only); audit per call; rate limits; tool schema
  versioning; returned third-party text marked as data (`untrusted_quote`). Research first: `docs/research/mcp.md`.
- **Evals:** tools verified from Claude Desktop and ChatGPT; isolation tests (token of workspace A
  never reads B); policy tests; every answer carries evidence ids and deep links.
- **Dependencies:** C. A read-only PAT preview may run in parallel with D once C is done.
- **Exit:** ≥ 2 design partners use it weekly; directory submission ready.

## G — Delivery and proactivity

Smart native notifications with thresholds, pre-meeting delivery at lead time, scheduled briefs;
email or Slack delivery only where design partners ask. **Exit:** alert budget respected, no
complaint of spam, usefulness ≥ E's level.

## H — Monetization and release hardening

Billing (`BillingProvider`, Lemon Squeezy), plan limits, export, full deletion (verified), legal
pages, backups + restore drill, security review (authz, RLS, SSRF, webhook spoofing, prompt
injection, MCP), load test, Google OAuth verification + CASA, Microsoft publisher verification,
public-launch checklist. **Exit:** all launch gates passed or explicitly waived for a paid pilot.

## Later — data sources by hypothesis

No integration is built because it was once planned. Each needs a one-paragraph hypothesis naming
the intelligence gap observed with design partners (e.g. "delivery friction is discussed in Slack,
so risk is detected late for 3 of 5 partners"). Candidates (Slack is already a v1 source): call transcripts (Fathom,
Fireflies, Zoom, Meet, Teams), PM tools (Jira, ClickUp, Asana, Linear), CRM (HubSpot, Pipedrive,
Salesforce), time and invoices (Harvest, Toggl, Xero, QuickBooks, Stripe) for a later profitability
module, contracts, external company events and job postings.

## Owner decisions for this roadmap

| # | Decision | Status |
|---|---|---|
| R-D1 | Surfaces: native Mac app + MCP; `apps/web` removed (2026-10-02, `docs/plans/d-web-removal.md`) | owner direction 2026-09-30 and 2026-10-02 |
| R-D2 | The engine computes and persists every judgement; its read models are the only read layer for Mac and MCP (Local v1: the engine runs on the Mac) | lead default, updated 2026-10-05 |
| R-D3 | Mac stack: Swift 6 / SwiftUI, macOS 14+, no Electron/Tauri (see migration map) | lead default |
| R-D4 | Mac v1 English only, String Catalogs ready (the web app and its 5 locales are removed) | lead default, owner may change |
| R-D5 | Weekly email and Slack delivery deferred to G | lead default |
| R-D6 | Replay report via operator CLI for design-partner sessions before the Mac app | lead default |
| R-D7 | Real AI runs: explicit owner go per run; Firra pays for no model (Local v1) | owner rule, updated 2026-10-05 |
| R-D8 | Apple Developer Program (99 USD/yr) and Sparkle dependency | owner 2026-10-02: not now (DMG, local certificate) |
| R-D9 | Local v1: fully local, one user, no teams; extraction by the owner's own AI, verified by Firra | owner 2026-10-05 |
| R-D10 | MCP locally (stdio) and over a public link (relay, OAuth) for claude.ai and ChatGPT; no "always available" mode | owner 2026-10-05 |
| R-D11 | Sources in v1: Gmail, Google Calendar, Slack (owner's own Slack app, user token, polling) | owner 2026-10-05 |
| R-D12 | No Apple Developer account for now (R-D8 stands); GitHub Actions manual only, checks local | owner 2026-10-05 |
| R-D13 | G1 (real-mail test before building) skipped; the first pilot partner is the first real mail | owner 2026-10-05 |
