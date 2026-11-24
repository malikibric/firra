# P0 security review (2026-09-28)

Scope: everything shipped in P0 — migrations and RLS, access RPCs, `apps/web` auth and server
actions, `apps/api` auth, rate limiting, webhooks and task enqueueing, `services/worker` task
authentication, Terraform IAM, CI/CD. Method: code review plus probes against staging where a
behaviour depends on Google's front end.

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| F1 | Medium | Rate limiter and audit IP used the **first** `X-Forwarded-For` entry, which the client controls. Staging probe: 150 requests with spoofed values, 0 throttled (control without the header: 25 throttled). | **Fixed**: use the last entry, which Cloud Run's front end appends. Regression test in `apps/api/test/rate-limit.test.ts`; re-probed on staging after deploy. |
| F2 | Low | Supabase session cookies were readable by JavaScript (`httpOnly: false` default) although the browser never uses a Supabase client. | **Fixed**: `httpOnly`, `sameSite=lax`, `secure` on https (`apps/web/src/lib/supabase/cookie-options.ts`). |
| F3 | Check | Could unauthenticated callers learn the worker's payload schema from validation errors? | **No**: auth runs before body validation; test added. |
| F4 | Low | Invite tokens are in the invite URL path and therefore in Cloud Run request logs. | **Accepted for now**: tokens are single-use, expire in 7 days and only work for the invited, confirmed email; logs stay in the EU project. Revisit with the log-retention work in P9. |

## Verified without findings

- RLS on every public table, `workspace_id not null`, no anon access, no direct user writes, no
  PUBLIC `EXECUTE` (pgTAP `00_rls_coverage`), cross-workspace isolation (`01_isolation`).
- SECURITY DEFINER functions pin `search_path`; invite tokens are 256-bit, stored as sha256,
  single-use, email-bound (confirmed email required); last-owner rule holds under concurrency
  (row lock).
- Web: all data access through the user's session; open-redirect guard on `next`; nonce-based
  CSP with `strict-dynamic`; security headers; server actions re-check the session and rely on
  RPC authorization for every mutation.
- api: Supabase JWT verification, workspace role check without existence leaks, Google ID token
  allowlist for `/internal/*`, constant-time HMAC webhook verification with replay window, raw
  body size limit, generic 500s.
- Worker: Cloud Tasks OIDC (issuer, audience, service account, `email_verified`), local HS256
  mode refused outside local/test, job updates scoped by `(workspace_id, id)`, SQL guard test.
- Logs: allowlisted fields only; free text redacted; exceptions logged by class.
- Infra: least-privilege service accounts, worker internal ingress plus IAM invoker, WIF pinned to
  repository id and GitHub environment, EU-only secrets and buckets, no secret values in state.

## Follow-ups (tracked for later milestones)

- Worker connects to Postgres as `postgres` via the pooler. Use a dedicated role with only the
  grants the worker needs (P9 hardening).
- Per-instance rate limiting is approximate across Cloud Run instances; add Cloud Armor or a
  shared limiter before launch (P9).
- Migrate the Supabase project to asymmetric JWT signing keys so sessions verify locally.
- Audit `ip` / `user_agent` come from headers our server forwards; they are informational and
  must never drive authorization.
