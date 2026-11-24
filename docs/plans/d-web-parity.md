# Web parity checklist for the Mac app (S2)

> **Cloud-first history.** The order of work is now `docs/plans/local-v1.md`.


Source: `apps/web/src` on branch `docs/d-mac-plan`; API routes in `apps/api/src/routes`;
RPCs in `supabase/migrations`. Every row verified in code. The setup web app
(`apps/web/AGENTS.md`) is setup-only: sign-in, onboarding, connections, clients,
services, members, contributors, audit. There are no product screens (no portfolio,
attention, briefs, decisions) — the Mac app does not need to replace what does not exist.

Conventions below: "Role" is what the backend enforces (the Mac app must enforce the
same by calling the same RPC/endpoint as the same user). All web reads use the user's
Supabase session (RLS); the web app never uses the service role
(`apps/web/src/lib/supabase/server.ts`).

## 1. Sign-in and sign-out

| Web page / action | Does | RPC / endpoint | Role | Details a rewrite could forget |
|---|---|---|---|---|
| `/{locale}/login` magic-link form (`login/actions.ts:sendMagicLink`) | Sends a sign-in link to the email | `supabase.auth.signInWithOtp`, `emailRedirectTo = {APP_URL}/auth/callback?next=…`, `shouldCreateUser: true` | n/a (unauthenticated) | Email lowercased, validated `z.email().max(254)`; `next` re-validated with `safeNextPath` (same-origin path only); success shows "link sent to {email}" state, never auto-redirects |
| `/{locale}/login` Google / Microsoft buttons (`signInWithProvider`) | OAuth sign-in via Supabase Auth | `signInWithOAuth` provider `google` / `azure`; azure gets `scopes: "openid email profile"`; same `next` handling | n/a | Failure redirects to `/{locale}/login?error=oauth` → generic error alert; provider value parsed by `z.enum(["google","azure"])` |
| Sign-out (header button, invite page) (`[locale]/actions.ts:signOut`) | Signs out, redirects to login | `supabase.auth.signOut()` | signed-in user | Locale-preserving redirect to `/{locale}/login` |
| `/auth/callback` (`app/auth/callback/route.ts`) | Completes magic-link/OAuth PKCE code exchange, redirects to `next` | `exchangeCodeForSession(code)` | n/a | `next` sanitized via `safeNextPath`, fallback `/{defaultLocale}`; any failure → `/{locale}/login?error=link` (locale parsed from `next`, else `en`) |

## 2. Entry and onboarding

| Web page / action | Does | RPC / endpoint | Role | Details a rewrite could forget |
|---|---|---|---|---|
| `/` (`[locale]/page.tsx`) | Entry: first workspace → overview, else onboarding | `workspace` select order by `created_at` limit 1 (RLS: members only) | member (any role) | Requires session first (`requireUser` → login with `next` preserved); non-members get "not found" behaviour downstream, never a "forbidden" page |
| `/{locale}/onboarding` create-workspace (`onboarding/actions.ts`) | Creates workspace, makes caller owner, redirects into it | `rpc("create_workspace", {p_name, p_timezone, p_default_locale})` → workspace id | any signed-in user | Name trim, 1–120 chars; timezone must be in `Intl.supportedValuesOf("timeZone")` or `"UTC"` (browser zone is only the default, server re-validates); locale must be one of `en de sv fr it`; DB `22023` → "invalid timezone" message; redirect to `/{locale}/w/{id}` |
| `/w/{id}` overview (`w/[workspaceId]/page.tsx`) | 4-step setup checklist + budget notice + links | Reads: `client`, `client_domain`, `connection`, `job` (backfill/upload types only), `llm_budget_event` (current UTC month, highest threshold) | member | Counting rules in `lib/setup.ts`: only `active` connections; only `active` clients that have ≥1 domain; jobs only for active connections; import status = needsAttention (any failed) / waiting (0 jobs) / ready (all succeeded) / inProgress. Budget alert: threshold 100 → error tone ("processing paused"), 80/50 → info; states "threshold reached", never a spend percentage. Privacy note + audit-log link on the page |

## 3. Clients

| Web page / action | Does | RPC / endpoint | Role | Details a rewrite could forget |
|---|---|---|---|---|
| Clients page list (`clients/page.tsx`) | Lists clients + their domains for editing | `client` select + `client_domain` select, ordered by name/domain | member | Form remounts (clears) after each added client (`key={clients?.length}`); empty state text when no clients |
| Create client (`clients/actions.ts:createClientAction`) | Creates client with domains + policy in one transaction | `rpc("create_client", {p_workspace_id, p_name, p_policy, p_domains})` | owner/admin (RPC `42501`) | Name 1–200; domains parsed by `lib/domains.ts` (comma/space/semicolon split, strip scheme/`www.`/path, lowercase, dedupe; whole input rejected if any entry invalid); policy `full`/`metadata_only`/`excluded`; choosing `excluded` requires the `confirmExclusion` checkbox; error mapping: "public email domains" message → publicDomain, "another client" → domainTaken; page revalidated on success |
| Edit client (`updateClientAction`) | Renames, changes status, replaces domains, optionally changes policy | 3 sequential RPCs: `update_client` (name+status), `set_client_domains`, `set_client_policy` (only when changed) | owner/admin | Status `active`/`churned`/`prospect` (create form has no status field — new clients are active by default); exclusion checkbox required only when moving *to* `excluded` from another policy; policy help text follows the selected policy (`policyHelp_{policy}`); on mid-sequence error the page revalidates and reports, earlier steps stay applied (not atomic across the three calls) |

## 4. Connections (own mailbox, uploads, org-wide)

| Web page / action | Does | RPC / endpoint | Role | Details a rewrite could forget |
|---|---|---|---|---|
| Connect Google / Microsoft mailbox (`connections/actions.ts:connectMailbox`) | Starts personal OAuth for the signed-in user's own mailbox | `POST /v1/workspaces/{id}/connections/{google\|microsoft}/start` with `return_to` (user session token) | owner/admin | Redirects to the provider consent URL only if it starts with `https://accounts.google.com/` or `https://login.microsoftonline.com/` (`lib/oauth.ts`); otherwise back to connections with `?connect_error=`: `{provider}_not_configured` or `start_failed` |
| Connections list (`connections/page.tsx`) | Shows connections, backfill progress, sync state | `connection`, `job` (`sync.backfill_chunk`, `ingest.upload`), `contributor` (owner email lookup) | member | Progress counts jobs per connection: total/done(`succeeded`)/failed(`failed`); "N of M months" for mail, separate upload wording for `email_upload`; `last_synced_at` formatted medium+short; `token_revoked` → "reconnect" alert; `?connected` → success alert; `?connect_error=` mapped against a 10-value allowlist, unknown → generic `start_failed`; owner label: "you" / contributor email / "team" |
| Mail-export upload (`upload-form.tsx` + `startUpload`/`completeUpload`) | Uploads `.mbox`/`.eml` (≤200 MB) direct to storage, then queues import | `POST /v1/workspaces/{id}/uploads` → `{upload_url, object}`; browser PUTs file; `POST …/uploads/complete` → 202 | owner/admin | Client pre-check 200 MB (`MAX_BYTES`, mirrors API `MAX_UPLOAD_BYTES`); format from extension (`.eml` → eml, else mbox), picker accepts `.mbox,.eml`; PUT is `XMLHttpRequest` with percent progress; URL must start with `https://storage.googleapis.com/` or `{API_URL}/local-uploads/`; on queued import the page refreshes; errors: tooLarge / notConfigured / failed |
| Org access page (`connections/org/page.tsx`) | Shows delegation prerequisites + per-provider mailbox forms | `POST /v1/workspaces/{id}/org/status` → `{google: {client_id, scopes}\|null, microsoft: {tenant_id\|null}\|null}` | owner/admin (API enforces; 403 → "forbidden" alert) | Info notice always shown; consent/error banners from `?org_consent` / `?org_error=no_tenant\|tenant_mismatch\|access_denied` (unknown → generic); Google section shows client id + scopes and the mailbox form only when configured; Microsoft shows "needs own connection" until `tenant_id` exists, then consent button + mailbox form |
| Add org mailboxes (`org/actions.ts:addOrgMailboxes`) | Adds up to 100 mailboxes via delegation/app permissions | `POST …/org/{google\|microsoft}/mailboxes` with `{emails}` → per-email `added`/`not_found`/`consent_missing` | owner/admin | Emails split on whitespace/comma/semicolon, 1–100 or `invalidEmails`; 400→invalidEmails, 503→notConfigured, 409→noTenant, else generic; results listed per email |
| Microsoft admin consent (`grantMicrosoftConsent`) | Sends the admin to the tenant consent screen | `POST …/org/microsoft/consent` with `return_to` → `consent_url` | owner/admin | Redirects only to `https://login.microsoftonline.com/…`; else back with `?org_error=no_tenant` (409) or `failed` |

## 5. Contributors (team sources without app access)

| Web page / action | Does | RPC / endpoint | Role | Details a rewrite could forget |
|---|---|---|---|---|
| Invite contributor (`contributors/actions.ts:inviteContributor`) | Creates invite, shows a one-time connect link | `rpc("create_contributor_invite", {p_workspace_id, p_email, p_name})` → `{token}`; link = `{APP_URL}/{locale}/connect/{token}` | owner/admin | Email lowercased, `z.email().max(254)`; name optional free text; link shown once in a success alert with copy affordance (`data-testid="contributor-link"`); re-inviting a revoked address re-arms it (migration `20260928000010`, lines 55-56) |
| Contributor list + revoke (`contributors/page.tsx`, `revokeContributor`) | Lists `id, email, name, status`; revokes access | `contributor` select; `rpc("revoke_contributor", {p_contributor_id})` (UUID-validated) | owner/admin | Revoke button only for non-revoked rows; revoke is immediate, no confirmation dialog in web |
| Contributor connect page (`connect/[token]/page.tsx`) | No-app-access page: data-collection notice, own connections, connect/disconnect | `POST /connect/contributor/status` (no session; token must match `/^[0-9a-f]{64}$/`, else "invalid" card) | invite-token holder | **The 7-bullet notice is mandatory**: scope (which mailboxes), discard of non-client data, per-client policy, 15-day retention, no employee scoring, read-only (drafts never auto-send), disconnect effect (`NOTICE_KEYS`). Shows connected accounts, `?connected` / `?connect_error=` banners (only `not_configured` distinguished, rest generic). Invalid/unknown token → invalid title+body, no further UI |
| Contributor connect/disconnect (`connect/[token]/actions.ts`) | Starts provider OAuth for the contributor; disconnects all own sources | `POST /connect/contributor/{google\|microsoft}/start`; `POST /connect/contributor/disconnect` | invite-token holder | Same provider-origin allowlist as member connect; disconnect button rendered only when ≥1 active connection; disconnect revalidates the page (no redirect) |

## 6. Service catalog

| Web page / action | Does | RPC / endpoint | Role | Details a rewrite could forget |
|---|---|---|---|---|
| List + create service (`settings/services/`) | Firm's service catalog used for latent-need matching | `workspace_service` select; `rpc("save_workspace_service", {p_workspace_id, p_service_id: null, …})` | owner/admin (RPC) | Name 1–120 required; description ≤1000 (optional); keywords via `lib/service-keywords.ts`: split on comma/newline, trim+lowercase, dedupe, drop empties, ≤20 entries and ≤1000 raw chars or `keywordsInvalid`; duplicate name → `duplicate` (23505); form remounts after each addition |
| Edit service | Same form prefilled per row | `save_workspace_service` with `p_service_id` | owner/admin | `p_service_id: null` means create — the call casts this one field (generated DB types mark it required); invalid UUIDs → generic error without a DB call |
| Delete service (`ServiceDeleteForm`) | Deletes a catalog entry | Existence check (`workspace_service` select by id+workspace) then `rpc("delete_workspace_service", {p_service_id})` | owner/admin | Requires the `confirmDelete` checkbox (HTML `required` + server check `=== "on"`); unknown id → generic error (no existence leak) |

## 7. Members and invites

| Web page / action | Does | RPC / endpoint | Role | Details a rewrite could forget |
|---|---|---|---|---|
| Member list (`settings/members/page.tsx`) | Members with email + role, pending invites with expiry | `rpc("list_workspace_members", {p_workspace_id})`; `workspace_invite` select (pending, unexpired, newest first) | member (reads); management below is owner/admin | "You" marker on own row; remove button only when viewer is owner AND row is not self; invite list shows `expires {date}` (medium date) |
| Invite admin (`members/actions.ts:createInvite`) | Creates admin invite (7-day expiry), emails it, shows copyable link | `rpc("create_workspace_invite", {p_workspace_id, p_email})` → `{token}`; link `{APP_URL}/{locale}/invite/{token}`; email via Resend (`lib/email.ts`, subject/body from `InviteEmail` translations) | owner/admin creator (RPC forbids duplicates → `alreadyMember`) | Email lowercased+validated; workspace id UUID-validated (else `invalidEmail`); result shows link always + "emailed" note only if Resend succeeded (missing `RESEND_API_KEY`/`EMAIL_FROM` → `emailed: false`, still shows link — no silent loss) |
| Revoke invite (`revokeInvite`) | Cancels a pending invite | `rpc("revoke_workspace_invite", {p_invite_id})` | owner/admin | No confirmation in web; list revalidates |
| Remove member (`removeMember`) | Removes an owner/admin | `rpc("remove_member", {p_workspace_id, p_user_id})` | owner only (button hidden otherwise; RPC also guards last-owner removal → `lastOwner`/`P0001` "owner" message) | No confirmation in web |
| Accept invite (`invite/[token]/`) | Signed-in user with matching confirmed email joins as admin, lands in workspace | `rpc("accept_workspace_invite", {p_token})` → workspace id; token must be 64-hex or `invalid` | signed-in user whose email matches invite | Page requires login first (`next=/{locale}/invite/{token}` preserved); shows "you sign in as {email}" + accept button + ghost sign-out (wrong-account escape); errors: `invalid` (bad/expired/used token), `wrongEmail` (42501 "different email"), generic; success redirects to `/{locale}/w/{id}` |

## 8. Audit log

| Web page / action | Does | RPC / endpoint | Role | Details a rewrite could forget |
|---|---|---|---|---|
| Audit view (`w/[workspaceId]/audit/page.tsx`) | Paged workspace event history | `audit_log` select of `id, created_at, action, actor_type, actor_id, target_type, target_id` only (never metadata/ip/user-agent) + `list_workspace_members` for email lookup | owner/admin (RLS policy) | `PAGE_SIZE = 50`, `MAX_PAGE = 200`; `?page=` must match `/^[1-9]\d{0,2}$/` and ≤200 else page 1; deterministic order `created_at` then `id` desc; fetch `PAGE_SIZE+1` (`range(..., page*PAGE_SIZE)`) to know "older"; actor rendering: member email, else raw id + label; `mcp` → "MCP token" label; else system; target shown as `{type} · {id}` only when both present |

## 9. Read-only API surface with no web UI (Mac app scope decision)
These exist in `apps/api` but the setup web app has no screen for them. A native app
replacing web parity does not need them; building them is new scope:
- `GET /v1/workspaces/{id}/attention` (owner/admin, audited) — no web page.
- MCP token create/list/revoke (`/v1/workspaces/{id}/mcp/tokens`, `apps/api/src/routes/mcp-tokens.ts`)
  and `POST /mcp` — no web page.
- `POST /diagnostics/ping`, `/diagnostics/llm-probe` (internal), `GET …/jobs/{jobId}`.

## MISSING list (backend exists, web has no function — Mac app must decide)
1. `claim_contributor_invite(p_token)` (`20261002000020`, authenticated, invited/active only, email must match confirmed account email) — the Mac contributor sign-in flow needs this; web contributors use the token link only and never sign in.
2. `my_contributions()` (own active contributor rows: workspace id+name, contributor id, status) — Mac contributor home needs this; web has no equivalent (contributors get no workspace list page).
3. `disconnect_own_connection(p_connection_id)` (contributor revokes one own connection, wipes token, audited) — web contributors can only disconnect *all* via `/connect/contributor/disconnect`; per-connection disconnect is Mac-only so far.
4. `record_decision_feedback`, `record_client_outcome`, `delete_client_outcome`, `request_replay_run` (owner/admin RPCs, `20260930000017`) — no web UI at all; only relevant if the Mac app grows product screens.
5. Workspace settings beyond defaults: web sets timezone + default locale once at creation (`create_workspace`); there is no edit-workspace, no export/delete-workspace, no billing screen in web. If the Mac app needs them, they are new backend work too.
6. Connection removal: web shows connections but offers no delete/revoke button for members (only contributor self-disconnect and token-revoked notice); per-connection revoke from the owner side would be new.

## Web-only behaviour (not user functions) and the Mac replacement
| Web behaviour | File | Mac replacement |
|---|---|---|
| Locale routing: `/{locale}` prefix always, 5 locales `en de sv fr it` (`routing.ts`, `localePrefix: "always"`), `pageLocale()` validation, `not-found.tsx` | `i18n/routing.ts`, `page-locale.ts`, `proxy.ts` | No URL locales. Use device locale with `en` fallback for the same 5 catalogs; validate any stored locale against the same list |
| Edge proxy per request: CSP nonce header, next-intl routing, Supabase cookie session refresh (`getClaims`), authz left to pages/actions | `proxy.ts` (matcher skips `auth/`, `health`, statics) | None. No cookies, no CSP, no middleware. Supabase iOS client holds the session (Keychain); refresh handled by the SDK |
| Cookie session: httpOnly + `secure` on https + `sameSite: lax`, `path: /`; server-only reads; Server Components cannot set cookies so the proxy refreshes | `supabase/cookie-options.ts`, `supabase/server.ts` | Token storage in Keychain via Supabase Auth natively; never in UserDefaults; send `Authorization: Bearer` per request |
| `/auth/callback` PKCE code exchange + `next` sanitizing | `app/auth/callback/route.ts`, `lib/safe-redirect.ts` | OAuth/magic-link deep-link handler in-app (`firra://auth/callback` equivalent): exchange code for session, validate the post-login destination against an allowlist of in-app routes (same rule: no off-app redirect from a `next` value) |
| Magic-link `emailRedirectTo` and OAuth `redirectTo` back to web callback | `login/actions.ts:callbackUrl` | Point them at the API or app deep link; keep `shouldCreateUser: true` semantics and the azure `openid email profile` scopes |
| `requireUser` → login redirect with `next` preserved on every page/action | `lib/auth.ts` (JWT verified via `getClaims`) | Route guard: unauthenticated → sign-in screen carrying the pending destination; re-resolve after session restore |
| Server-to-API bridge with 15 s timeout, `cache: no-store`, 503 on network failure | `lib/api.ts:callApi` | Same envelope in `URLSession` config: 15 s timeout, no response caching for API calls, map transport errors to a "service unavailable" state |
| OAuth `return_to` round-trip and `authorizationOrigin` allowlist (google/microsoft only) | `connections/actions.ts`, `connect/[token]/actions.ts`, `lib/oauth.ts` | Open consent in `ASWebAuthenticationSession` with the same two-origin allowlist; validate `return_to`/state the same way; surface the same 10 `connect_error` codes |
| Upload via presigned PUT from the client with progress | `upload-form.tsx` (`XMLHttpRequest` PUT, `application/octet-stream`) | `URLSessionUploadTask` with progress delegate; same 200 MB pre-check, same `.mbox`/`.eml` detection, same target-URL allowlist before sending bytes |
| Contributor invite-link token links (`/connect/{64-hex}`) usable without sign-in | `connect/[token]/page.tsx` | Universal-link handling for the same path (no session required); keep the 64-hex check client-side before the status call and the same invalid-link screen |
| Copy-to-clipboard for invite/contributor links | `invite-form.tsx`, `contributors/invite-form.tsx` (`navigator.clipboard`) | `UIPasteboard` copy button next to each link; same "shown once" semantics for the contributor token link |
| Resend invite email from the server action (link always shown even if email unsent) | `members/actions.ts`, `lib/email.ts` | Same rule: the link is the source of truth; "email sent" is informational. Keep the 10 s send timeout behaviour (fail → show link with "not emailed" state) |
| `revalidatePath` after every mutation instead of local state | all `actions.ts` | Refetch the affected query after each mutation (clients, contributors, members, services, connections, audit) |
| Health endpoint `/health` | `app/health/route.ts` | Not needed in-app; operator concern stays on web/API |
