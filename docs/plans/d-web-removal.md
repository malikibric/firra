# Removing apps/web (milestone D, T42)

> **Cloud-first history.** The order of work is now `docs/plans/local-v1.md`.


Owner direction: the Mac app is the only UI (D-D1). Analysis by Muse Spark (S9, 2026-10-02), checked
and corrected by Claude; the full file:line inventory is in the session notes, the decisions and the
order are here.

## Decisions (Claude, 2026-10-02; owner may overrule)

| # | Decision | Why |
|---|---|---|
| W1 | **Contributor token-link flow is dropped** (`/connect/contributor/*` routes without a session, `contributor` connect link, status/disconnect-by-token). Contributors have accounts now (D-D3) and use the Mac app. | Two ways in is two ways to get wrong; the token page only existed because contributors had no account. |
| W2 | **Org-wide access pages (Google delegation, Microsoft application permissions) are not rebuilt in the Mac app yet.** The API routes stay (`/v1/.../org/*`); the Mac screens come when a customer needs org mode. User-connect (D-D4) covers the pilot. | Not on the pilot path; keeping the routes loses nothing. |
| W3 | **Billing and legal pages** have no web home. Billing is P8 and not built; legal text goes to the system browser (static pages) when it exists. Nothing is lost by deleting web. | No code to preserve. |
| W4 | Invite emails stop linking to the web. The email carries the **invite code** and one line "open Firra for Mac, sign in, enter this code"; the API response keeps `token`, `link` becomes optional and is no longer produced. | The Mac asks for a code, not a link. |
| W5 | Playwright e2e is retired with the web app. Coverage moves to the API integration tests (CI, local Supabase), pgTAP and the Swift tests. | No browser UI left to drive. |

## Order (each PR green on its own)

| PR | Task | Content | Notes |
|---|---|---|---|
| 1 | done | `firra://` return path in `safeReturnPath` (T62, #95); DB check on `oauth_state.return_to` (#104) | |
| 2 | T78 | API: `WEB_URL` optional; invite emails with the code (W4); remove contributor token routes, their store methods and tests (W1); OAuth callbacks never need a web origin; upload origin for GCS resumable init no longer the web URL | Codex; after #95 merges. Mac: `AdminInviteCreated.link` optional |
| 3 | T79 | CI (`ci.yml` web jobs, Playwright artifact), `deploy.yml` loop and health check; Terraform: web Cloud Run service, web env and secret bindings, IAM, uptime check, storage CORS origin, `urls.web` output | Claude (infra); staging first; owner updates the branch ruleset after the first green run |
| 4 | T80 | `rm -rf apps/web packages/ui packages/i18n`; `.next` turbo outputs; `tsconfig.next.json`; lockfile regenerated | after PR 3 is applied |
| 5 | T81 | Docs: CLAUDE.md, architecture, roadmap, local-dev, staging-setup, `.env.example`; parity docs marked superseded | last, so no doc describes a state that does not exist |

## Risks to watch (from the analysis)

- Delete order matters: API (no web origin needed) → CI and deploy → Terraform → code. Dropping `WEB_URL`
  from Terraform before PR 2 would crash the API on boot.
- Supabase Auth Site URL / redirect list: keep `firra://auth-callback`; remove the web URL only after
  PR 4. A wrong edit locks everyone out of sign-in.
- GCS resumable uploads send an `Origin`; set the new value before removing the CORS rule and verify with
  a real large `.mbox` upload.
- `sentry-dsn-web` and the `resend-api-key` binding for web: remove the *binding*, never the secret value.
- `terraform output urls | jq -r .web` in runbooks and operator scripts.
