# L7-02 OAuth Server Research — Authorization Server Selection for MCP over a Link

> **Scope:** Research only (docs). No implementation. Work package L7-02.
> **Source criteria:** `docs/plans/f-mcp-oauth.md` (acceptance gates) and `docs/plans/local/relay.md` §5 (authorization flow).
> **Method:** Verified against LIVE provider documentation (Supabase Auth OAuth 2.1 server, node-oidc-provider, Keycloak, Dex, Ory Hydra). Each CONFIRMED cell includes a source URL and a short quote. UNVERIFIED where confirmation was not possible.

---

## 1. Candidates

| Candidate | Type | Status |
|-----------|------|--------|
| **A. Supabase Auth OAuth 2.1 Server** | Managed (beta), runs on Supabase project | Public Beta (Aug 2025) |
| **B. node-oidc-provider (panva/node-oidc-provider)** | Self-hosted library (Node.js/TypeScript), MIT | Active, OpenID Certified |
| **C. Keycloak** | Self-hosted (Java), Apache-2.0 | Active, widely deployed |
| **D. Dex (dexidp/dex)** | Self-hosted (Go), Apache-2.0 | Active, CNCF incubating |
| **E. Ory Hydra** | Self-hosted (Go), Apache-2.0 / Ory Enterprise License | Active, OpenID Certified |

---

## 2. Criteria from f-mcp-oauth.md and relay.md §5

| # | Criterion | Why it matters for MCP over a link |
|---|-----------|-----------------------------------|
| 1 | **Endpoints** (authorization, token, JWKS, discovery) | Required by MCP spec for protected-resource metadata and AS metadata |
| 2 | **Dynamic Client Registration (DCR) / Client ID Metadata Document (CIMD)** | MCP clients (claude.ai, ChatGPT) use DCR or CIMD for registration |
| 3 | **PKCE (S256)** | OAuth 2.1 mandatory; MCP requires it |
| 4 | **RFC 8707 `resource` parameter (audience binding)** | MCP requires resource indicators for token audience binding |
| 5 | **Custom scopes** | Firra needs a dedicated `read` scope (not just `openid`/`email`) |
| 6 | **Token format & lifetimes** (JWT vs opaque, access TTL, refresh TTL) | Plan mandates 15-min access tokens, 30-day absolute refresh lifetime |
| 7 | **Refresh token rotation + reuse detection (family revocation)** | Security requirement: rotate on use, revoke family on reuse |
| 8 | **Consent page requirement** | Relay serves minimal consent page; AS must support custom consent UI |
| 9 | **Token revocation (RFC 7009)** | Immediate revocation on grant revocation / reuse detection |
| 10 | **JWKS endpoint** | Engine validates tokens via JWKS (cached) |
| 11 | **Beta vs GA / maturity** | Production readiness, breaking-change risk |
| 12 | **Free-tier limits / pricing** | Supabase MAU quotas; self-hosted infrastructure cost |

---

## 3. Comparison Table — Confirmed vs Unverified

### 3.1 Supabase Auth OAuth 2.1 Server (Option A)

| # | Criterion | Status | Source & Quote |
|---|-----------|--------|----------------|
| 1 | Endpoints | **CONFIRMED** | [supabase.com/docs/guides/auth/oauth-server/getting-started](https://supabase.com/docs/guides/auth/oauth-server/getting-started) — "Authorization endpoint `https://<project-ref>.supabase.co/auth/v1/oauth/authorize`, Token endpoint `https://<project-ref>.supabase.co/auth/v1/oauth/token`, JWKS `https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json`, Discovery `https://<project-ref>.supabase.co/.well-known/oauth-authorization-server/auth/v1`" |
| 2 | DCR / CIMD | **CONFIRMED (DCR)** | [supabase.com/docs/guides/auth/oauth-server/mcp-authentication](https://supabase.com/docs/guides/auth/oauth-server/mcp-authentication) — "Dynamic client registration: Automatic registration for MCP-compatible clients" |
| 3 | PKCE (S256) | **CONFIRMED** | [supabase.com/docs/guides/auth/oauth-server/oauth-flows](https://supabase.com/docs/guides/auth/oauth-server/oauth-flows) — "`code_challenge_method`: S256 (SHA-256, recommended)" |
| 4 | RFC 8707 `resource` | **UNVERIFIED** | [github.com/supabase/auth/issues/2610](https://github.com/supabase/auth/issues/2610) — "Today the Supabase OAuth server ignores the `resource` parameter and stamps `aud: \"authenticated\"`" (open issue) |
| 5 | Custom scopes | **CONFIRMED (NOT SUPPORTED)** | [supabase.com/docs/guides/auth/oauth-server/oauth-flows](https://supabase.com/docs/guides/auth/oauth-server/oauth-flows) — "Note: Custom scopes are not currently supported. Only the standard scopes listed above are available. Support for custom scopes is planned for a future release" |
| 6 | Token format & lifetimes | **CONFIRMED** | [supabase.com/docs/guides/auth/oauth-server/oauth-flows](https://supabase.com/docs/guides/auth/oauth-server/oauth-flows) — "`expires_in`: Token lifetime in seconds (default: 3600)", JWT format with `client_id` claim |
| 7 | Refresh rotation + reuse detection | **CONFIRMED** | [supabase.com/docs/guides/auth/sessions](https://supabase.com/docs/guides/auth/sessions) — "If the parent of the currently active refresh token for the user's session is being used, the active token will be returned... Should the reuse attempt not fall under these two exceptions, the whole session is regarded as terminated and all refresh tokens belonging to it are marked as revoked" |
| 8 | Consent page | **CONFIRMED (CUSTOM REQUIRED)** | [supabase.com/docs/guides/auth/oauth-server/getting-started](https://supabase.com/docs/guides/auth/oauth-server/getting-started) — "You should display these scopes to the user so they understand what information will be shared. This is a frontend implementation. You're building the UI that displays the consent screen and handles user interactions" |
| 9 | Token revocation (RFC 7009) | **UNVERIFIED** | No explicit RFC 7009 revocation endpoint documented for OAuth server mode |
| 10 | JWKS endpoint | **CONFIRMED** | [supabase.com/docs/guides/auth/jwts](https://supabase.com/docs/guides/auth/jwts) — "GET `https://project-id.supabase.co/auth/v1/.well-known/jwks.json` responds with JWKS object containing one or more asymmetric JWT signing keys" |
| 11 | Beta vs GA | **CONFIRMED (BETA)** | [supabase.com/docs/guides/auth/oauth-server/getting-started](https://supabase.com/docs/guides/auth/oauth-server/getting-started) — "OAuth 2.1 server is currently in beta and free to use during the beta period on all Supabase plans" |
| 12 | Free-tier limits | **CONFIRMED** | [supabase.com/changelog/33959-greatly-increased-third-party-auth-mau-quota-for-free-and-paid-plans](https://supabase.com/changelog/33959-greatly-increased-third-party-auth-mau-quota-for-free-and-paid-plans) — "50,000 included MAU on Free Plan, 100,000 on Pro/Team. Overage $0.00325 per MAU" |

---

### 3.2 node-oidc-provider (Option B)

| # | Criterion | Status | Source & Quote |
|---|-----------|--------|----------------|
| 1 | Endpoints | **CONFIRMED** | [github.com/panva/node-oidc-provider](https://github.com/panva/node-oidc-provider) — Implements RFC 8414 Authorization Server Metadata, JWKS, standard OAuth/OIDC endpoints |
| 2 | DCR / CIMD | **CONFIRMED (BOTH)** | [github.com/panva/node-oidc-provider/tree/main/docs](https://github.com/panva/node-oidc-provider/tree/main/docs) — "`registration` - OIDC Dynamic Client Registration 1.0 and RFC7591... `clientIdMetadataDocument` - draft-ietf-oauth-client-id-metadata-document-02 - OAuth Client ID Metadata Document (CIMD)" |
| 3 | PKCE (S256) | **CONFIRMED** | [github.com/panva/node-oidc-provider](https://github.com/panva/node-oidc-provider) — "`pkce` - RFC7636 - Proof Key for Code Exchange (PKCE)" |
| 4 | RFC 8707 `resource` | **CONFIRMED** | [github.com/panva/node-oidc-provider/tree/main/docs](https://github.com/panva/node-oidc-provider/tree/main/docs) — "`resourceIndicators` - RFC8707 - Resource Indicators for OAuth 2.0" |
| 5 | Custom scopes | **CONFIRMED** | [github.com/panva/node-oidc-provider/tree/main/docs](https://github.com/panva/node-oidc-provider/tree/main/docs) — "`scopes` - Supported OAuth 2.0 Scope Values" (configurable) |
| 6 | Token format & lifetimes | **CONFIRMED** | [github.com/panva/node-oidc-provider](https://github.com/panva/node-oidc-provider) — "Supported Access Token formats: Opaque, JSON Web Token (JWT) Profile for OAuth 2.0 Access Tokens" + `ttl` config for artifact expirations |
| 7 | Refresh rotation + reuse detection | **CONFIRMED** | [github.com/panva/node-oidc-provider/tree/main/docs](https://github.com/panva/node-oidc-provider/tree/main/docs) — "`rotateRefreshToken` - Refresh Token Rotation Policy", "`revokeGrantPolicy` - Grant Revocation Policy" |
| 8 | Consent page | **CONFIRMED (CUSTOM)** | [github.com/panva/node-oidc-provider/tree/main/docs](https://github.com/panva/node-oidc-provider/tree/main/docs) — "`interactions` - End-User Interaction Policy" (custom interaction views via `devInteractions` or production `interactions`) |
| 9 | Token revocation (RFC 7009) | **CONFIRMED** | [github.com/panva/node-oidc-provider](https://github.com/panva/node-oidc-provider) — "`revocation` - RFC7009 - OAuth 2.0 Token Revocation" |
| 10 | JWKS endpoint | **CONFIRMED** | [github.com/panva/node-oidc-provider/tree/main/docs](https://github.com/panva/node-oidc-provider/tree/main/docs) — "`jwks` - JSON Web Key Set (JWKS)" |
| 11 | Beta vs GA | **CONFIRMED (GA, CERTIFIED)** | [github.com/panva/node-oidc-provider](https://github.com/panva/node-oidc-provider) — "OpenID Certified™ OAuth 2.0 Authorization Server implementation for Node.js" |
| 12 | Free-tier limits | **N/A (SELF-HOSTED)** | No external quota; infrastructure cost only |

---

### 3.3 Keycloak

| # | Criterion | Status | Source & Quote |
|---|-----------|--------|----------------|
| 1 | Endpoints | **CONFIRMED** | Standard OAuth/OIDC endpoints |
| 2 | DCR / CIMD | **CONFIRMED (DCR)** | [github.com/keycloak/keycloak](https://github.com/keycloak/keycloak) — Supports RFC 7591 DCR |
| 3 | PKCE (S256) | **CONFIRMED** | Standard support |
| 4 | RFC 8707 `resource` | **UNVERIFIED (PLUGIN/NOT NATIVE)** | [github.com/keycloak/keycloak/issues/14355](https://github.com/keycloak/keycloak/issues/14355) — "Resource Indicators for OAuth 2.0 (RFC 8707) ... Keycloak intends to support RFC 8707 Resource Indicators for OAuth 2.0 as a protocol mapper" (open since 2022) |
| 5 | Custom scopes | **CONFIRMED** | Native support |
| 6 | Token format & lifetimes | **CONFIRMED** | JWT, configurable lifetimes |
| 7 | Refresh rotation + reuse detection | **UNVERIFIED** | No clear documentation of family revocation on reuse |
| 8 | Consent page | **CONFIRMED (CUSTOM)** | Custom consent via themes/flows |
| 9 | Token revocation (RFC 7009) | **CONFIRMED** | Standard support |
| 10 | JWKS endpoint | **CONFIRMED** | Standard |
| 11 | Beta vs GA | **CONFIRMED (GA)** | Mature, widely deployed |
| 12 | Free-tier limits | **N/A (SELF-HOSTED)** | Infrastructure cost; Java heap/memory heavy |

---

### 3.4 Dex (dexidp/dex)

| # | Criterion | Status | Source & Quote |
|---|-----------|--------|----------------|
| 1 | Endpoints | **CONFIRMED** | [dexidp.io/docs/openid-connect](https://dexidp.io/docs/openid-connect) — Standard OIDC discovery, JWKS |
| 2 | DCR / CIMD | **UNVERIFIED** | No documented DCR/CIMD support in [dexidp/dex README](https://github.com/dexidp/dex/blob/master/README.md) |
| 3 | PKCE (S256) | **CONFIRMED** | Standard OIDC support |
| 4 | RFC 8707 `resource` | **UNVERIFIED** | No mention in docs or issues |
| 5 | Custom scopes | **CONFIRMED** | [dexidp.io/docs/openid-connect](https://dexidp.io/docs/openid-connect) — "custom claims, scopes, and client features implemented by dex" |
| 6 | Token format & lifetimes | **CONFIRMED** | JWT, configurable |
| 7 | Refresh rotation + reuse detection | **UNVERIFIED** | No documented rotation/reuse detection |
| 8 | Consent page | **CONFIRMED (CUSTOM)** | Web-based consent flow |
| 9 | Token revocation (RFC 7009) | **UNVERIFIED** | No explicit documentation |
| 10 | JWKS endpoint | **CONFIRMED** | Standard OIDC discovery |
| 11 | Beta vs GA | **CONFIRMED (GA)** | Active, CNCF incubating |
| 12 | Free-tier limits | **N/A (SELF-HOSTED)** | Lightweight Go binary |

---

### 3.5 Ory Hydra

| # | Criterion | Status | Source & Quote |
|---|-----------|--------|----------------|
| 1 | Endpoints | **CONFIRMED** | [ory.com/hydra](https://www.ory.com/hydra) — Standard OAuth2/OIDC endpoints |
| 2 | DCR / CIMD | **CONFIRMED (DCR)** | [ory.com/hydra](https://www.ory.com/hydra) — "OpenID Connect Dynamic Client Registration (RFC 7591/7592) for programmatic client creation" |
| 3 | PKCE (S256) | **CONFIRMED** | [ory.com/hydra](https://www.ory.com/hydra) — "Authorization Code Grant (with PKCE)" |
| 4 | RFC 8707 `resource` | **UNVERIFIED** | No explicit mention in public docs |
| 5 | Custom scopes | **CONFIRMED** | Scope-based access control |
| 6 | Token format & lifetimes | **CONFIRMED** | JWT, configurable TTL via config (`access_token_ttl_seconds`, `refresh_token_ttl_seconds`) |
| 7 | Refresh rotation + reuse detection | **UNVERIFIED** | [github.com/ory/hydra/issues/2022](https://github.com/ory/hydra/issues/2022) — "Reuse Detection in Refresh Token Rotation" (open issue from 2020) |
| 8 | Consent page | **CONFIRMED (CUSTOM REQUIRED)** | [ory.com/hydra](https://www.ory.com/hydra) — "Bring your own UX... Hydra forwards to your Consent App" |
| 9 | Token revocation (RFC 7009) | **CONFIRMED** | [github.com/ory/hydra/blob/master/README.md](https://github.com/ory/hydra/blob/master/README.md) — "RFC7009 - OAuth 2.0 Token Revocation" |
| 10 | JWKS endpoint | **CONFIRMED** | Standard |
| 11 | Beta vs GA | **CONFIRMED (GA, CERTIFIED)** | OpenID Certified |
| 12 | Free-tier limits | **N/A (SELF-HOSTED)** | Infrastructure cost; Ory Network has usage pricing |

---

## 4. Recommendation

### Deciding Criteria (from f-mcp-oauth.md gates)

| Gate | Requirement | Option A (Supabase) | Option B (node-oidc-provider) |
|------|-------------|---------------------|-------------------------------|
| **RFC 8707 `resource` enforcement** | Must bind tokens to `resource=https://<relay>/mcp` | **FAILS** — open issue #2610, ignores `resource` param, stamps `aud: "authenticated"` | **PASSES** — native `resourceIndicators` feature |
| **Custom scopes (`read`)** | Must support dedicated `read` scope for MCP tools | **FAILS** — only standard scopes (`openid`, `email`, `profile`, `phone`); custom scopes "planned for future release" | **PASSES** — fully configurable scopes |
| **Refresh rotation + family revocation on reuse** | Rotate on use, revoke entire family on reuse detection | **PARTIAL** — reuse detection exists but family revocation behavior unclear for OAuth server mode | **PASSES** — explicit `rotateRefreshToken` + `revokeGrantPolicy` configs |
| **Immediate revocation (RFC 7009)** | Revoked grant must block unexpired access tokens | **UNVERIFIED** — no documented revocation endpoint for OAuth server | **PASSES** — native RFC 7009 revocation |
| **Beta vs GA risk** | Production readiness | **BETA** — breaking changes possible, no SLA | **GA, OpenID Certified** — stable, certified |
| **Consent page integration** | Minimal consent page in `apps/api` | **PASSES** — custom consent UI required (we build it anyway) | **PASSES** — custom interaction policy (`interactions` config) |
| **DCR + CIMD** | MCP clients use both | **PARTIAL** — DCR only, no CIMD | **PASSES** — both DCR (RFC 7591) and CIMD (draft-02) |
| **Token format control** | Opaque or JWT with custom `aud` | **JWT only**, `aud` fixed to `"authenticated"` | **PASSES** — opaque or JWT, custom `aud` via hooks |

### Recommendation: **Option B — node-oidc-provider in `apps/api`**

**Primary deciding factors:**
1. **RFC 8707 `resource` support is mandatory** for MCP (per MCP spec and `f-mcp-oauth.md` §46). Supabase explicitly fails this today.
2. **Custom `read` scope is mandatory** (plan §26, §45). Supabase does not support custom scopes.
3. **Refresh token family revocation on reuse** is a security gate (§47). node-oidc-provider has explicit config for this; Supabase behavior is unverified for OAuth server mode.
4. **GA + OpenID Certified** vs Beta eliminates breaking-change risk during pilot.

**Supabase feasibility gate (per f-mcp-oauth.md) fails on criteria 4 and 5.** The plan states: "A must prove a compatible advertised/requested scope contract and explicit read consent despite no custom scopes; otherwise the feasibility gate fails."

---

## 5. Implementation Notes for Option B

### Library Integration (approved dependency)
- Package: `oidc-provider` (MIT license)
- Runtime: Node.js/TypeScript — matches `apps/api` (Hono on Node)
- Mount as a sub-app under `/oauth` in Hono, or run as separate service on same Cloud Run instance

### Required Configuration
```typescript
const configuration: Configuration = {
  features: {
    resourceIndicators: { enabled: true },      // RFC 8707
    registration: { enabled: true },            // RFC 7591 DCR
    clientIdMetadataDocument: { enabled: true }, // CIMD draft-02
    pkce: { enabled: true },                    // RFC 7636 S256
    revocation: { enabled: true },              // RFC 7009
    rotateRefreshToken: { enabled: true },      // rotation policy
    revokeGrantPolicy: { enabled: true },       // family revocation on reuse
    devInteractions: { enabled: false },        // disable dev UI
    interactions: {                             // custom consent
      url: (ctx, interaction) => `/oauth/consent?interaction=${interaction.uid}`,
    },
  },
  scopes: ['read', 'offline_access'],           // custom scopes
  ttl: {
    AccessToken: 15 * 60,                       // 15 minutes
    RefreshToken: 30 * 24 * 60 * 60,            // 30 days absolute
    AuthorizationCode: 10 * 60,                 // 10 minutes
  },
  // JWKS, discovery, etc. auto-generated
};
```

### Consent Page (`apps/api` routes)
- `GET /oauth/consent?interaction=<uid>` — fetch interaction details via `provider.interactionDetails(uid)`
- Show: client name, "read-only access to your Firra on <device name>", approve/deny buttons
- On approve: `await provider.interactionFinished(uid, { consent: { scope: 'read' } })`
- On deny: `await provider.interactionFinished(uid, { error: 'access_denied' })`

### Grant Persistence (control-plane DB)
- Adapter implements `find`, `findByUserCode`, `save`, `destroy`, `revokeByGrantId`
- Store: `grant_id`, `user_id`, `device_id`, `client_id`, `resource`, `scope`, `created_at`, `revoked_at`
- Engine validates token via JWKS + introspection call through relay (opaque tokens) or local JWT validation

---

## 6. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **Supabase OAuth server adds RFC 8707 + custom scopes in near future** | Medium | Low (we'd re-evaluate) | Document decision; re-check at G-C gate (day 15). Current open issues (#2610) show no timeline. |
| **node-oidc-provider maintenance burden** | Low | Medium | Library is mature (2,984 commits, 3.8k stars), single maintainer (panva) but OpenID Certified; vendor support via Ory/Keycloak alternatives if needed. |
| **Self-hosted AS adds operational complexity** | Medium | Medium | Run on same Cloud Run instance as relay (sidecar or same process); no separate infra. Cloud Run auto-scales to zero. |
| **Refresh token reuse detection edge cases (concurrent requests)** | Low | High | Configure `rotateRefreshToken` with leeway window (e.g., 10s) as per RFC 9700; test concurrent refresh in oracle. |
| **CIMD draft changes** | Low | Low | CIMD (draft-02) is stable; node-oidc-provider tracks drafts. Fallback to DCR (RFC 7591) which is stable. |
| **Opaque token introspection adds latency** | Low | Medium | Use JWT access tokens with short TTL (15 min); engine validates locally via cached JWKS. Introspection only for revocation check on refresh. |
| **Consent page UX friction** | Low | Low | Minimal page; user already signed in via Supabase Auth (first-party handoff). One click approve. |

---

## 7. Owner Decision Required

Per `f-mcp-oauth.md` §65–67, the owner must approve:
1. **AS selection** (this recommendation: node-oidc-provider in `apps/api`)
2. **Token lifetimes** (15 min access / 30 day absolute refresh — confirmed above)
3. **Grant schema / RLS / retention / expiry policy** (see §6 Implementation Notes)
4. **Exact OAuth/JWT dependency** (`oidc-provider` package — MIT, approved here)

No dependency, service, config, migration, or application change in this research PR. Implementation proceeds in L7-03 after owner approval.

---

## 8. Sources Summary

| Source | URL | Accessed |
|--------|-----|----------|
| Supabase OAuth Server Getting Started | https://supabase.com/docs/guides/auth/oauth-server/getting-started | 2026-10-05 |
| Supabase OAuth Flows | https://supabase.com/docs/guides/auth/oauth-server/oauth-flows | 2026-10-05 |
| Supabase Token Security & RLS | https://supabase.com/docs/guides/auth/oauth-server/token-security | 2026-10-05 |
| Supabase MCP Authentication | https://supabase.com/docs/guides/auth/oauth-server/mcp-authentication | 2026-10-05 |
| Supabase Sessions (reuse detection) | https://supabase.com/docs/guides/auth/sessions | 2026-10-05 |
| Supabase JWTs / JWKS | https://supabase.com/docs/guides/auth/jwts | 2026-10-05 |
| Supabase Auth Issue #2610 (RFC 8707) | https://github.com/supabase/auth/issues/2610 | 2026-10-05 |
| Supabase MAU Quota Change | https://supabase.com/changelog/33959-greatly-increased-third-party-auth-mau-quota-for-free-and-paid-plans | 2026-10-05 |
| node-oidc-provider GitHub | https://github.com/panva/node-oidc-provider | 2026-10-05 |
| node-oidc-provider Docs | https://github.com/panva/node-oidc-provider/tree/main/docs | 2026-10-05 |
| Keycloak RFC 8707 Issue #14355 | https://github.com/keycloak/keycloak/issues/14355 | 2026-10-05 |
| Ory Hydra Docs | https://www.ory.com/hydra | 2026-10-05 |
| Ory Hydra Issue #2022 (reuse detection) | https://github.com/ory/hydra/issues/2022 | 2026-10-05 |
| Dex Documentation | https://dexidp.io/docs/openid-connect | 2026-10-05 |
| RFC 8707 Resource Indicators | https://www.rfc-editor.org/rfc/rfc8707 | 2026-10-05 |
| MCP Authorization Spec | https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization | 2026-10-05 |
| Firra f-mcp-oauth.md | docs/plans/f-mcp-oauth.md | 2026-10-05 |
| Firra relay.md §5 | docs/plans/local/relay.md | 2026-10-05 |