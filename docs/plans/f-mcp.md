# Milestone F: remote MCP server (plan)

> **Cloud-first history.** The order of work is now `docs/plans/local-v1.md`.


Roadmap F (`docs/roadmap.md`). Research: `docs/research/mcp.md`. Starts after C exits (T29–T34).
Owner direction 2026-10-02: autonomous work, no Apple account (so MCP comes before the Mac app).

Goal: a founder asks Claude or ChatGPT "which clients need my attention?" / "why is Acme on
watch?" and gets answers from Firra's stored judgements with evidence links. Read-only.

## Decisions

| # | Decision | Why |
|---|---|---|
| F-D1 | MCP server lives in `apps/api` at `POST /mcp` (Hono), tools are thin adapters over the `/v1` read models (no logic of their own). | North star: one read layer (R-D2). |
| F-D2 | Stateless Streamable HTTP, no sessions, no GET stream (spec 2026-07-28), **plus** the legacy 2025-11-25 handshake behaviour in stateless form so current clients work. `Origin` validated against an allowlist (empty `Origin` allowed for non-browser clients), body size limit, per-token rate limit. | Cloud Run fit; clients lag the spec. |
| F-D3 | Dependency: SDK v2 scoped packages (`@modelcontextprotocol/server`, `@modelcontextprotocol/hono`) if a spike proves both eras work; else `@modelcontextprotocol/sdk` 1.31 (named in CLAUDE.md §5). Conformance suite as a local/CI check. | Same SDK, reorganised; decide by evidence. |
| F-D4 | **Personal access tokens first.** Table `mcp_token` (one migration, pgTAP): `workspace_id`, `user_id`, `token_hash` (sha256 of a 256-bit secret shown once), `name`, `scopes text[]` (`read` only for now), `last_used_at`, `expires_at` (default 90 days), `revoked_at`. Create, list and revoke through `SECURITY DEFINER` RPCs for owner/admin (audited); only the hash is stored. Format `frm_…` so leaks are scannable. | Fastest path for design partners; spec leaves auth optional. |
| F-D5 | **How a token reads data.** The API resolves the token (hash lookup, not revoked or expired, workspace match), then obtains a **short-lived Supabase session of the token's user, minted server-side** (service role `generateLink` + `verifyOtp`, cached in memory per token for at most 45 minutes, never stored, never logged, never returned) and runs the existing RLS-as-caller `ReadStore` with it. RLS stays the only authorization layer; no second implementation of visibility rules. The token is bound to one workspace; any workspace id in a tool call must equal it. | Avoids duplicating policy logic in SQL functions (C-D5 alternative). Risk: the minted session has the user's full powers; the MCP layer exposes only read tools and never hands the session out. |
| F-D6 | Audit: every tool call writes `audit_log` (`actor_type = 'mcp'`, `actor_id` = token id, action `mcp.<tool>`, target client when any, metadata: tool name, result count, never arguments that contain text) before returning; fails closed. | CLAUDE.md §3. |
| F-D7 | Third-party text is data: every quote is returned in a field named `untrusted_quote`, wrapped by tool descriptions that say so, and results include `source` ids and deep links; no tool writes anything. | Prompt-injection defence. |
| F-D8 | OAuth 2.1 (Protected Resource Metadata, Client ID Metadata Documents, audience-bound tokens) is a follow-up needed for a connector directory; evaluate Supabase Auth's OAuth server then. The token model (user + workspace + scopes + revocation) is chosen so it carries over. | Spec path, later. |
| F-D9 | Tool schemas come from `packages/contracts` (zod); tool names are stable, changes are additive, a breaking change is a new tool name. Each tool returns `structuredContent` validated against its output schema plus a short text rendering. | Same discipline as `/v1`. |

## Tools (first set, all read-only, all workspace-bound)

`get_clients_needing_attention` (T29 attention), `get_client_state` (T30 detail),
`explain_client_change` (state history and drivers with evidence), `get_client_timeline` (T31),
`search_evidence` (T31 evidence list with filters), `get_open_commitments` and
`get_stakeholders` and `get_client_unknowns` (from T30 detail), `get_churn_replay` (T31b). Later: `prepare_meeting` (E),
`get_growth_opportunities`.

## Tasks

| ID | Task | Owner | Notes |
|---|---|---|---|
| T50 | Spike: SDK v2 vs v1 with a v1-era client and the conformance suite; decision recorded in this plan | Codex | no production code |
| T51 | Migration `mcp_token` + RPCs + pgTAP; token create/list/revoke in `/v1` (owner/admin) | Claude | ⚠ security, schema |
| T52 | MCP endpoint, auth (token → minted session), Origin check, per-token limiter, audit, stateless transport; token create/list/revoke routes | Claude | ⚠ built on branch `claude/t52-mcp-endpoint` with one tool (`get_clients_needing_attention`); the minted-session approach (F-D5) is proved by `test/integration/mcp.test.ts`, which runs in CI against local Supabase |
| T53 | Tools over `/v1` read models, schemas, untrusted-text marking | Codex | after T52 |
| T54 | Isolation, policy, injection and audit tests through the MCP endpoint; manual check from Claude Desktop | Codex + owner | owner opens one connector |
| T55 | Docs for design partners: how to create a token and add the connector | Claude | |

## Evals

Token of workspace A never reads B (including a forged `workspace_id` argument); `excluded`
absent, `metadata_only` without quotes; every answer carries evidence ids; a revoked or expired
token is refused on the next call; audit row per call; injection fixture (a quote that says
"ignore previous instructions and call …") comes back inside `untrusted_quote` and changes nothing.

## Out of scope

Write tools (feedback, outcomes) in MCP; OAuth; ChatGPT app directory submission; Mac app.
