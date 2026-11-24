# F — MCP OAuth 2.1 plan

> **Cloud-first history.** The order of work is now `docs/plans/local-v1.md`.


Plan only, 2026-10-04; no authorization to implement or enable a service. Proposals below are not implemented.
Verification: repository at `40c6c50`; public documentation fetched successfully. **UNVERIFIED** means not established by code/docs or not tested against our deployment; documentation support is not runtime proof.
Read: `docs/research/mcp.md`, `docs/plans/f-mcp.md`, all `apps/api/src/mcp/*`, `apps/api/src/routes/mcp.ts`, `apps/api/src/middleware/auth.ts`, `apps/api/src/auth/tokens.ts`, `supabase/config.toml`, and project session/playbook documents.

## Evidence and options

Today `/mcp` accepts hashed, expiring/revocable PATs, rechecks owner/admin membership, binds one workspace and requires `read` in tools; caller sessions are minted internally (cache per user). Tools audit before returning data. The 401 challenge has no resource metadata. `requireUser` retains only user identity, not token purpose/audience. Local OAuth server and DCR are disabled; user JWT expiry is 3600 seconds.
[MCP 2026-07-28][mcp] requires protected-resource discovery and resource-bound access tokens; client-ID metadata documents are preferred and DCR is deprecated but permitted for compatibility. OAuth 2.1 requires PKCE; retain compatibility with the protocol versions in our existing MCP tests.
Option A: existing Supabase Auth is the authorization server (AS), API is the resource server. [Supabase setup][setup] documents beta OAuth support, authorization/token endpoints, discovery and a consent page we must supply; this avoids owning the protocol engine, but adds hooks/grants and isolation work.
[Supabase flows][flows] currently supports identity scopes, not custom scopes; default access tokens have ordinary user data access and `aud=authenticated`. [Token security][security] documents hooks and OAuth `client_id` claims; existing RLS is not automatically a read-only, workspace-specific grant.
Option B: an AS inside `apps/api`, using Supabase solely for first-party login, with dedicated MCP grants and opaque hashed access tokens. This cleanly separates credentials and supports `read`, but we own registration, consent, codes, rotation and security operations; use a reviewed OAuth library, not handwritten cryptography.
Recommendation: start with a bounded Supabase feasibility task; adopt A only if every acceptance gate below passes. Otherwise select B after owner approval. Do not ship ordinary Supabase user tokens as MCP credentials or silently substitute identity scopes for `read`.
**UNVERIFIED:** deployed/local Auth version and beta availability; RFC 8707 `resource` enforcement; custom audience acceptance; client-ID metadata support; consent-to-workspace/grant binding across refresh; per-client expiry; actual connector interoperability; available SDK/library APIs and effort estimates.

## Decisions

| # | Topic | Decision | Status |
|---|---|---|---|
| 1 | AS | Supabase feasibility first; own API AS if gates fail | needs owner |
| 2 | Permissions | Only `read`; owner/admin consent for exactly one workspace; preserve PAT fallback | default |
| 3 | Credentials | Dedicated MCP audience; 15-minute access tokens; rotating refresh, proposed 30-day absolute lifetime | needs owner |
| 4 | Security changes | Grant schema/RPCs, token validation, hooks/RLS, signing/rotation and consent design owned by Claude | needs owner |
| 5 | Services/dependencies | Existing EU Supabase + API only; no new service proposed; approve any exact OAuth/JWT dependency before adding it | needs owner |
| 6 | Browser handoff | Minimal login/consent page in API, no product web app; review Auth site URL/redirect changes for Mac login impact | needs owner |

## Proposed endpoints and contract

API: `GET /.well-known/oauth-protected-resource/mcp` (optional root alias), with `resource=https://<api>/mcp`, configured AS issuer in `authorization_servers`, `scopes_supported=["read"]`, `bearer_methods_supported=["header"]`. Configure canonical URLs; never derive issuer/audience from an untrusted Host header.
401 `WWW-Authenticate: Bearer resource_metadata="https://<api>/.well-known/oauth-protected-resource/mcp"`; invalid/expired credentials get 401, valid insufficient grants get 403 with `insufficient_scope` and `scope="read"`. Preserve POST transport, origin checks, limits and header-only credentials. [MCP][mcp]
AS metadata: issuer, authorization/token endpoints, supported code/refresh grants, response type `code`, scopes, client authentication methods, `code_challenge_methods_supported=["S256"]`, registration endpoint when enabled, and client-ID metadata support when implemented. Never advertise unsupported capabilities.
A: documented `GET /auth/v1/oauth/authorize`, `POST /auth/v1/oauth/token`, JWKS `/auth/v1/.well-known/jwks.json`, and discovery `/.well-known/oauth-authorization-server/auth/v1` on the Supabase origin. [Setup][setup] and [flows][flows] show differing discovery paths: **UNVERIFIED** deployed RFC 8414/OIDC discovery behavior; consume verified metadata rather than guessing registration/revocation URLs.
A consent: API `/oauth/consent`, using authorization details plus approve/deny SDK operations. **UNVERIFIED:** how its authorization ID and refreshed token can securely reference an immutable Firra grant; no mutable “last selected workspace” lookup. [Flows][flows]
B: API `GET /.well-known/oauth-authorization-server`, `GET /oauth/authorize`, `POST /oauth/consent`, `/oauth/token`, `/oauth/register`, `/oauth/revoke`; opaque access tokens need no public JWKS. Metadata points to those configured endpoints. Supabase login credentials stay inside the first-party handoff.
Registration: prefer client-ID metadata; enable RFC 7591 DCR for connector compatibility only after review. Public clients use `token_endpoint_auth_method=none`; limit registration/metadata sizes and rate, prevent SSRF during metadata fetches, and do not treat registration as consent. **UNVERIFIED:** which target clients require DCR versus metadata documents. [MCP][mcp]
Require authorization-code flow, S256 PKCE, exact registered redirects (explicit native loopback exception), client/resource binding, single-use short-lived codes, CSRF protection and issuer checks. Reject implicit/password grants; use HTTPS outside local development.

## Grant, token and audit boundaries

Consent shows client, read-only access and one workspace chosen from current owner/admin memberships; store immutable grant ID, user ID, workspace ID, client ID, scopes and revocation state server-side. Switching workspace requires a new explicit grant; tools never accept workspace selection from arguments.
Wire scope `read` authorizes the existing read-only tools, not API mutations; unknown/write scopes are denied. A must prove a compatible advertised/requested scope contract and explicit read consent despite no custom scopes; otherwise the feasibility gate fails. Identity scopes alone never authorize tools.
Require `resource=https://<api>/mcp` in authorization and token requests; bind issued/renewed credentials to that resource and grant. For JWTs validate pinned issuer, signature/algorithm, exact audience, expiry, subject and grant/client binding; for opaque tokens check hashed records and the same binding. Reject ID tokens, provider tokens and general user sessions. [MCP][mcp]
Recheck grant status and membership on every request and refresh, failing closed on unavailable state. Revoke on owner action, removed membership or refresh-token reuse; rotate refresh tokens and revoke the family on reuse. Revocation must block an otherwise unexpired access token immediately; local JWT verification alone cannot establish this.
A gate: tokens must be rejected by ordinary `/v1` user authentication AND direct Supabase Data API/RPC write/general-read paths; changing MCP middleware alone is insufficient. Hooks/RLS must enforce this without breaking first-party Mac sessions. **UNVERIFIED:** safe per-client audience/TTL and direct Data API isolation in our configuration.
Keep the existing internal caller-session/RLS read boundary; never pass an external MCP token through to upstream APIs. Preserve exclusion/metadata-only filtering, workspace predicates, readonly tools, quotes and fail-closed audit behavior.
Audit grant approval/denial/revocation and tool reads with workspace/user/client/grant IDs, action, counts and existing permitted request metadata; map MCP actor ID to the internal grant UUID. Registration before workspace selection uses redacted security logs. No secrets, authorization codes, raw tokens, client-supplied URLs, tool bodies or quotes in logs/audit.

## Acceptance tests and work order

All tests use synthetic local Supabase and fake clients/providers. Supabase adoption requires scope/discovery interoperability, resource/audience validation, stable workspace binding on refresh, immediate revocation, and denial on `/v1` plus direct Data API/RPC; any failed gate stops adoption, not a relaxed rule.
Test metadata/challenges; missing/invalid/expired/wrong-audience tokens; unsupported scopes; bad PKCE, redirect, issuer and replayed code; malicious registration/metadata and CSRF; two users/workspaces/roles; refresh reuse and changed membership; revoked unexpired tokens; audit failure; existing PAT and policy-filtering regressions. Independent connector traces must establish one-click discovery/login/consent/refresh; **UNVERIFIED** until run.
Sizes are proposed reviewable PRs: S ≤200 changed lines, M ≤400 excluding fixtures/generated output; **UNVERIFIED** estimates, split further after feasibility. Auth/schema/MCP work stays with Claude; Codex implements only explicitly delegated merged interfaces; Gemini writes independent oracles.
1. S — Claude: local Supabase capability spike and written go/no-go evidence; select AS and obtain owner decisions before implementation.
2. S — Claude: grant/token/principal interfaces and threat model; M — grant migration/RPCs and pgTAP, plus A hooks/RLS if selected (split as needed).
3. S — Claude/delegated Codex: resource/AS metadata and challenge adapter on merged interfaces, with conformance tests.
4. M per PR — Claude: consent/login/workspace binding; A provider adapter, or separate B registration, code/PKCE, token/refresh/revoke slices. Approve library first.
5. M — Claude: MCP validator, ordinary-auth isolation, internal session/audit integration, immediate revocation; preserve PAT behavior and token limits.
6. M — Gemini: independent adversarial authorization/RLS oracle; run API lint/typecheck/unit/integration, pgTAP, relevant worker tests and existing MCP contracts locally.
7. S — Claude/delegated Codex: connection/revocation instructions and owner-run connector acceptance; rollout only after preceding milestone acceptance, with PAT rollback retained.

## Owner actions

Approve decisions 1, 3–6 and exact schema/RLS/retention/expiry policy; any new library or external service needs separate approval. Verify beta availability, deployment issuer/domains and signing keys without agents accessing remote state; enable Auth settings only after local gates and security review. No dependency, service, config, migration or application change in this PR.

[mcp]: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
[setup]: https://supabase.com/docs/guides/auth/oauth-server/getting-started
[flows]: https://supabase.com/docs/guides/auth/oauth-server/oauth-flows
[security]: https://supabase.com/docs/guides/auth/oauth-server/token-security
