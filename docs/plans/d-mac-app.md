# Milestone D: Mac app as the only product surface (plan)

> **Cloud-first history.** The order of work is now `docs/plans/local-v1.md`.


Roadmap D (`docs/roadmap.md`), strategy in `docs/plans/mac-app.md`. Written 2026-10-02 after the owner's
decisions of the same day: **no product web app**; everything runs through the native Mac app;
contributors also sign up and sign in to the Mac app; no Apple Developer account (ad-hoc signed DMG,
manual updates, no Sparkle, no notarization). Supersedes the "web setup pages stay until parity"
wording: `apps/web` is retired when the Mac app covers its functions.

## Decisions

| # | Decision | Why |
|---|---|---|
| D-D1 | The Mac app is the only UI. `apps/web` is deleted after parity (T42); until then it keeps working untouched. Billing, legal pages and provider consent screens stay in the system browser. | Owner decision. |
| D-D2 | Sign-in: Supabase Auth through `ASWebAuthenticationSession` with PKCE, redirect `firra://auth-callback`, providers email magic link, Google, Microsoft. Tokens in the Keychain, refreshed before expiry. The only browser touch is the system sign-in sheet. | Standard, no web app needed. |
| D-D3 | **Contributors have accounts.** `contributor.user_id` (nullable, unique per workspace) links an invite to an auth user, claimed by RPC `claim_contributor_invite(token)` (confirmed email must match). A contributor is **not** a `membership` row: the many policies written as `is_member(workspace_id)` therefore never apply to them. They get exactly: read their own contributor row and their own connections, connect and disconnect their own sources, see the data-collection notice. API: `GET /v1/me` (user, memberships, contributor links) and `/v1/contributor/*` routes. | Keeps "no workspace data for contributors" structural, not a filter. |
| D-D4 | OAuth callbacks (Gmail, Outlook) redirect to `firra://connected?...` for sessions started from the app: `return_to` accepts the `firra://` scheme (and nothing else absolute); `WEB_URL` redirects stay only for the transitional web. | The provider consent page is the only browser step. |
| D-D5 | Setup API under `/v1` over the existing audited RPCs (no new write logic): workspace create, accept invite, clients (create, rename, domains, policy), services, members (list, invite, remove), contributors (invite, revoke), connections (list, start, disconnect, upload), replay request, MCP tokens (done). Same guard chain, error model, audit as C. | One write path. |
| D-D6 | `apps/mac` is a SwiftPM package first (library `FirraKit` with API client, auth, models, view models; executable target for the app; `swift test`), builds with the Command Line Tools and with Xcode. `scripts/package-mac.sh` builds the `.app`, ad-hoc signs it and makes the DMG. macOS 14+. | Verified: a SwiftUI `MenuBarExtra` app builds here without Xcode. |
| D-D7 | API client: hand-written `Codable` types and a thin `URLSession` client for the ~25 endpoints, with decoding tests against JSON fixtures produced from the contracts' zod examples (so a contract change fails a Swift test). `swift-openapi-generator` is reconsidered once the OpenAPI document covers every endpoint. | No new dependency before the shape settles. |
| D-D9 | **Mail lives on the owner's Mac; the server keeps only derived facts.** Owner decision 2026-10-02. Every user has their own account and their own local vault: cleaned mail bodies of the mailboxes they own (a member, or a contributor) are delivered to their Mac and stored there (encrypted, key in the Keychain). The server keeps what the team and MCP need: extractions, signals, assessments, evidence quotes (at most 25 words, our own rule for verifiable, minimal evidence), deep links, tokens (KMS-encrypted). A body is deleted from the server as soon as its record is **processed and delivered** to the owner's Mac; if the Mac has not collected it, 72 hours after ingest; stuck or unprocessed records never later than 7 days (the server can re-fetch from Gmail or Outlook). Signed-in users cannot read `body_clean` or `raw_object_uri` any more (column privileges); the only way out is the vault endpoint, which returns a caller's own mailboxes. Contributors' bodies never reach the founder, only the derived facts do. | Privacy promise without losing team views and the online MCP. |
| D-D8 | English only in v1 with String Catalogs from day one (R-D4). Notifications: local `UserNotifications` from polling; no APNs. | As roadmap. |

## Tasks

| ID | Task | Owner | Notes |
|---|---|---|---|
| T60 | `apps/mac` skeleton: package layout, API client core, Keychain, sign-in, session refresh, menu bar shell, "Connect AI tools" screen (create/list/revoke MCP token), "Needs attention" list | Claude | ⚠ auth; first thing a partner needs |
| T61 | Contributor accounts: migration (`contributor.user_id`, `claim_contributor_invite`, RLS for own connections only, `disconnect_own_connection`), pgTAP, `GET /v1/me`, `/v1/contributor/*` routes | Claude | ⚠ schema, RLS |
| T62 | Setup API (D-D5) and the `firra://` return path (D-D4) | Codex | after the plan is merged |
| T63 | Parity inventory of every `apps/web` function with its API/RPC equivalent (`docs/plans/d-web-parity.md`) | Spark | read-only, docs |
| T64 | Mac screens: setup (clients, policies, services, members, contributors, connections), client detail, evidence, replay, feedback | Claude + Codex | after T60, T62 |
| T65 | Contributor screens in the app (connect, disconnect, notice) | Codex | after T61 |
| T66 | `scripts/package-mac.sh`, DMG, Gatekeeper instructions for partners | Claude | |
| T67 | Server retention: ingest records the owning user (`vault_user_id`), hourly purge by the D-D9 rule, raw-object deletion, tests | Codex | after migration `20261002000022` (PR 92) |
| T68 | Vault API: `GET /v1/vault/records`, `POST /v1/vault/ack` (own mailboxes only, audited) | Codex | after T67 |
| T70 | Worker job `vault.rehydrate`: fetch a user's purged mail again from Gmail/Outlook (hash must match, retention clock restarts) | Codex | done (PR 92) |
| T71 | `POST /v1/vault/refetch`, one run per mailbox per day | Claude | done (branch `claude/t71-refetch`, PR after 92/93) |
| T72 | Mac: contributor home, Fetch my mail again | Spark + Claude | done (PR 98) |
| T69 | Mac vault: encrypted local store, sync loop, "Mail on this Mac" screen, evidence drawer reads local text | Claude | after T68 |
| T42 | Delete `apps/web` after parity, owner OK | Codex | last |

## Owner prerequisites

1. Xcode (free, App Store) installed: needed to run and debug the app comfortably; code and tests
   are built with SwiftPM meanwhile.
2. Supabase Auth: allow `firra://auth-callback` as a redirect URL (staging and prod). Claude does
   this when it can reach the project; otherwise it is one field in the dashboard.
3. No Apple Developer account, no Sparkle, no notarization: partners get a DMG and open it with
   right-click → Open (or "Open Anyway" in System Settings), updates are a new DMG.

## Out of scope for D

Mac App Store, APNs push, routines (E), MCP OAuth (F), localisation beyond English.

## Open gaps after the 2026-10-03 audit

The gap audit of 2026-10-03 (17 items) was worked off. Status of the last six, 2026-10-03:

| Gap | Status |
|---|---|
| Claim-level evidence for commitments, recurring asks and stakeholders | API: T91 (PR #118). Mac: T96 (quotes behind every commitment, goal and need). |
| Champion and "unknowns" on the client page | API: T91. Mac: T96. |
| A workspace-wide "What changed" feed | Done: API T90 (#115), MCP tool `get_recent_changes`, Mac screen T94 (#117). |
| Timeline paging and opening a signal's quotes | Done: T95 (#116). |
| String Catalogs (D-D8) | **Deferred by decision.** SwiftUI literals already behave as localisation keys; compiling an `.xcstrings` catalog into the app needs Xcode's toolchain, which the build Mac does not have, and a second language is not on the roadmap. Revisit when Xcode is installed or a second locale is requested. |
| UI tests for sign-in, expiry, offline | **Replaced by a manual release checklist** (`docs/runbooks/mac-release-checklist.md`, T97) run before each DMG, until Xcode (XCUITest) is available. |
