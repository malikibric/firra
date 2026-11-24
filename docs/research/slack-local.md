# Research: Slack rules and limits for a user-owned, local connector (L2S-01)

Date read: 2026-10-04. Tags: CONFIRMED = primary Slack source verbatim. UNVERIFIED = secondary/inferred/not fetched (no Slack API calls per brief).

Plan under review (`docs/plans/local-v1.md` T22–T25): user creates their own internal Slack app in their workspace from a Firra manifest link, installs it, pastes a **user token** (`xoxp-`) into Firra (Keychain). Sync = polling mapped channels every 5 min (`conversations.history` + `conversations.replies` for threads whose `latest_reply` changed) + daily 7-day re-scan for edits/deletes. No Events API, no Socket Mode, no Firra server in path. Fallback/backfill = Slack export `.zip` import.

## 1. Rate limits for our access pattern

**Answer: a user-created internal app in its own workspace is NOT subject to the 2025 Tier-1 squeeze. It keeps Tier 3 (50+/min, up to 1,000 objects). A Firra-distributed (even unlisted) app would be squeezed to 1 req/min + 15 objects. So T22's "user's own internal app" choice is load-bearing and correct.**

- Quote: "Effective May 29, 2025, all newly-created Slack apps that are commercially distributed and have not been approved for the Slack Marketplace will be subject to new rate limits for the conversations.history and conversations.replies API methods."
  - URL: https://docs.slack.dev/apis/web-api/rate-limits — 2026-10-04 — CONFIRMED
- Quote: "Apps already approved for the Slack Marketplace and internal customer-built applications should not see rate limit changes."
  - Same URL — CONFIRMED
- Quote: "The new rate limits will be updated to allow 1 request per minute and will return a maximum of 15 objects per request."
  - URL: https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps — 2026-10-04 — CONFIRMED
- Quote: "Internal customer-built applications are not impacted by these changes. For Custom apps, the conversations.history and conversations.replies API methods are limited to 50+ requests per minute. The maximum and default values for the limit parameter are 1,000 objects."
  - Same changelog — CONFIRMED
- Quote: "This method has different rates for non-Marketplace commercially distributed apps. As of May 29, 2025, for new applications and installation commercially distributed outside of the Marketplace, this method is rate limited to 1 request per minute. The maximum and default values for the limit parameter have both been reduced to 15 objects. For Marketplace and internal customer-built applications, this method has Tier 3 rate limits."
  - URL: https://docs.slack.dev/reference/methods/conversations.history — 2026-10-04 — CONFIRMED (identical note on `conversations.replies` per changelog; replies page not re-fetched — treat as CONFIRMED via changelog)
- Effective dates: Quote: "The updated terms are effective immediately for applications created on or after May 29, 2025. They will go into effect on June 30, 2025 for apps created before May 29, 2025." — same changelog — CONFIRMED
- Rationale: Quote: "To help keep workspace data secure and prevent bulk data exfiltration by unvetted applications, these methods will have a new rate limit of 15 messages per request at one request per minute." — same — CONFIRMED

Does a user-token internal app created from our manifest count as "internal customer-built"? CONFIRMED in substance, UNVERIFIED in label: the docs define the squeezed class as "commercially distributed outside of the Marketplace (also called 'unlisted' apps)" — we do NOT distribute an app at all (no app ID of ours, no install URL to our app; each user creates their own app object in their own workspace). That matches "internal customer-built", not "distributed". Caveat: ToS update says Quote: "the Slack Marketplace is the only appropriate channel for commercially distributing apps built with Slack APIs, whether those apps are unlisted ... or templatized one-off 'custom' apps." — URL: https://docs.slack.dev/changelog/2025/05/29/tos-updates — 2026-10-04 — CONFIRMED. Sharing a *manifest template* the user instantiates themselves is arguably "templatized one-off custom" distribution — UNVERIFIED whether Slack reads it that way. Cheapest settle: legal read of ToS + (if needed) one question to Slack support with the exact flow. Do NOT ship a Firra-owned app ID.

Practical backfill math (derived from CONFIRMED numbers, arithmetic UNVERIFIED as Slack statement):
- Tier 3 internal: 50+ req/min, up to 1,000 msgs/req. 20 channels × 3 months (~60–100k msgs worst case for active client channels) ≈ tens of requests with `limit=1000` + thread replies — minutes, fits 5-min poll + backfill. HOLDS.
- Tier 1 (if misclassified): 1 req/min × 15 msgs = 900 msgs/hour → same backfill takes days; 5-min polling of 20 channels impossible. This is why T22 must stay internal-only. If Slack ever reclassifies user-created-from-manifest apps as "distributed", plan falls back to export import (T23 fallback already).

## 2. API Terms and policies (storing, AI/LLM, consent, retention)

Source: Slack API Terms of Service, effective 2025-10-10 (current) + 2025-05-29 archive. URLs: https://slack.com/intl/en-in/terms-of-service/api and https://slack.com/policy-archives/terms-of-service-api/2025-05-29 — 2026-10-04 — quotes CONFIRMED.

- Outside-org vs inside-org distinction matters:
  - Quote: "If you offer your Application for use by others outside your organisation, you must obtain explicit authorisation from the organisation installing your Application for the use, processing and storage of API Data."
  - Quote: "You must limit your use, processing and retention of API Data from others outside your organisation to the minimum necessary to develop, test, operate and support your Application's functionality, in accordance with the Documentation."
  - Our case (user's own app in their own org, data stays on their Mac) is the inside-org case the paragraph does not directly regulate — but minimum-necessary + privacy-policy duties still apply (see below). Whether "user pastes their own token into their own Mac app" needs any org authorization beyond their membership: UNVERIFIED — default answer no (they act as the org member, not as a third-party provider), confirm with lawyer.
- No-training / no-bulk-export (provider-outside-org wording; treat as binding spirit for us):
  - Quote: "As the provider of an Application offered for use outside your organisation, you may not: (A) use API Data to train a large language model; (B) bulk export Slack message and file data except where expressly allowed by an additional agreement, such as by Applications using the Discovery API for approved security and compliance use cases; or (C) use data collected from one organisation to directly benefit a different organisation or any third party."
  - 2025-05-29 archive short form: Quote: "You may not: (A) use API Data to train a large language model; (B) bulk export Slack message and file data except where expressly allowed by an additional agreement..."
- Data Access / Real-Time Search API anti-stockpiling (analogous duty):
  - Quote: "When using these APIs as a third-party Application provider, you may not create persistent copies, archives, indexes or long-term data stores of other organisations' API Data."
  - Our storage is the user's own org's data on their own device under their control — not "other organisations'" — but the 12-month local retention (L8-01) + encrypted bodies must be disclosed and justified as minimum-necessary for the stated features. Lawyer to confirm wording.
- Privacy policy + transparency:
  - Quote: "Your privacy policy must meet applicable legal standards and describe the collection, use, storage and sharing of data in clear, understandable and accurate terms."
  - Must name Slack scopes, mapped-channel minimization, on-device storage + encryption, retention/deletion, user-directed AI processing with no-training expectation, relay exclusion (no Slack data to relay).
- Slack's own AI posture (context, not our permission):
  - Quote: "Your customer data (like messages and files) is never used to train any LLMs." — URL: https://slack.com/intl/en-in/trust/ai-principles — CONFIRMED (Slack's own models; does not grant us rights).

State plainly: no primary-source sentence was found that forbids a user-owned local app from storing that user's own reachable Slack messages on their own Mac, or from the user directing their own AI over them — but (a) the no-training + no-bulk-export + no-cross-org-benefit trio, (b) the "Marketplace is the only appropriate channel for commercially distributing apps ... or templatized one-off 'custom' apps" line, and (c) the user-consent/AI-provider-training problem (same as Google L0-04 §3) mean the plan needs a lawyer pass before F2b build. If any rule blocks it → fallback to export-only per brief Stop-and-ask (not triggered today: no blocking sentence found).

## 3. Internal apps — who can create, approval, free workspaces

- Quote: "By default, any member of a Slack workspace can install an app."
  - URL: https://slack.com/intl/en-in/help/articles/202035138-Add-apps-to-your-Slack-workspace — 2026-10-04 — CONFIRMED
- Quote: "This will not prevent members from creating and installing internal apps."
  - URL: https://slack.com/help/articles/1500009181142-Manage-app-settings-and-permissions (permission "Only allow apps from the Slack Marketplace") — 2026-10-04 — CONFIRMED (even the strictest Marketplace-only setting still allows internal app creation)
- Quote: "Workspace owners can turn on app approval to require admin approval for all apps." + "Members can install any apps approved by a Workspace Owner, and may be able to request approval for other apps. If an app has been restricted, members cannot install it or request approval for it."
  - Same add-apps URL — CONFIRMED
- What admin sees: approval queue with app name, requester, scopes; approve/restrict per workspace (see `admin.apps.approve` + "Manage app approval" docs) — CONFIRMED in substance (exact screen text UNVERIFIED; screenshot in pilot).
- Free workspaces differ: UNVERIFIED in this pass (creation/install rights on Free vs paid not pinned; export differs by plan per §9 but app-creation gating per plan not confirmed — check during L2S-14 test-workspace setup on a Free workspace).
- Runbook line for non-admin user: if approval is on, they request approval (custom message supported) and an owner approves scopes; if restricted, they cannot proceed → fallback export. CONFIRMED mechanism, UNVERIFIED exact copy.

## 4. App creation from a manifest (deeplink, schema, minimum user-token scopes)

- Shareable creation links — CONFIRMED:
  - Quote: "If you want to share your manifest as a link, you can use the following URL pattern: https://api.slack.com/apps?new_app=1&manifest_yaml=<manifest_here> Or to share the manifest in JSON format: https://api.slack.com/apps?new_app=1&manifest_json=<manifest_here> Ensure you URL encoded the YAML or JSON before sharing the URL."
  - URL: https://api.slack.com/reference/manifests — 2026-10-04 — CONFIRMED
  - Flow: Quote: "Choose to create an app from a manifest. Paste your manifest configuration in the input field provided, pick a development workspace, and click Next. Review ... and click Create." — same — CONFIRMED (copy/paste path always works; prefilled-link path is the one-click version of the same flow)
- Manifest schema: YAML/JSON bundle incl. `oauth_config.scopes.user[]` / `bot[]`, `redirect_urls`, display info — CONFIRMED via example:
  - Quote: "`oauth_config`: `scopes`: `user`: - `channels:history` - `reactions:read` ..." — URL: https://docs.slack.dev/reference/app-manifest — 2026-10-04 — CONFIRMED (example fragment)
- Minimum user-token scopes for Firra (read-only, user token `xoxp-`):
  - `channels:history` — Quote: "View messages and other content in public channels that your Slack app has been added to" — URL: https://docs.slack.dev/reference/scopes/channels.history/ — CONFIRMED
  - `groups:history` — Quote: "View messages and other content in private channels that your Slack app has been added to" — CONFIRMED (via api.slack.com/scopes/groups:history mirror)
  - `im:history` — Quote: "View messages and other content in direct messages that your Slack app has been added to" — URL: https://docs.slack.dev/reference/scopes/im.history — CONFIRMED
  - `mpim:history` — Quote: "View messages and other content in group direct messages that your Slack app has been added to" — URL: https://docs.slack.dev/reference/scopes/mpim.history — CONFIRMED
  - `channels:read`, `groups:read`, `im:read`, `mpim:read` for channel listing (`users.conversations` requires them: Quote "Scopes Bot token: channels:read groups:read im:read mpim:read / User token: channels:read groups:read im:read mpim:read" — URL: https://api.slack.com/methods/users.conversations — CONFIRMED)
  - `users:read` for names/identity + `users:read.email` for the email field (see §5) — CONFIRMED need per Slack Connect page (exact scope strings `users:read`, `users:read.email` standard; email-requirement quote in §5)
  - `chat.getPermalink`: Quote: "Scopes No scopes required" — URL: https://docs.slack.dev/reference/methods/chat.getPermalink/ — CONFIRMED
  - Each `*:history` exposes: "Compatible API methods conversations.history conversations.replies" — CONFIRMED per scope pages. Bot vs user token accessible-conversation table (user token = any private conversation the user is a member of + all public) — Quote in §1 history page: CONFIRMED.
  - Principle: request read scopes only; no `chat:write`, no bot token at all in v1 (user token only). Whether reviewers/admins prefer an additional narrow justification per scope: UNVERIFIED (workspace approval shows scopes; keep manifest minimal).

## 5. Slack Connect visibility + external identity

- Shared-channel visibility to user token: user token sees what the user sees, incl. shared channels they are in — CONFIRMED by token model ("User tokens represent the same access a user has to a workspace — the channels, conversations, users ... that they can see." — URL: https://docs.slack.dev/authentication/tokens.md — 2026-10-04 — CONFIRMED).
- External identity — CONFIRMED:
  - Quote: "If the user is a stranger who isn't in any shared channels, the is_stranger flag is set true."
  - Quote: "Both the users:read.email and users:read OAuth scopes are required to access the email field in user objects returned by the users.info and users.list API methods."
  - Quote: "Look for the is_stranger field in user objects. If it's true, your app does not share a channel with the user. If it's false, but the team associated with the user is not the installing team for your app, the user is external and your app does share a channel with them."
  - URL: https://api.slack.com/apis/connect (mirror http://docs.slack.dev/apis/slack-connect.md) — 2026-10-04 — CONFIRMED
- Channel flags: `is_shared`, `is_ext_shared` ("Indicates whether a conversation is part of a Shared Channel with a remote organization"), `connected_team_ids`, `context_team_id` — URL: https://docs.slack.dev/reference/objects/conversation-object — 2026-10-04 — CONFIRMED. Direction rule for L2S-06 (external team = client side; own team = firm) can key off `team_id` vs installing `team_id` + `is_ext_shared` — HOLDS, with R14 caveat (no visible email → map by channel, not email) intact.

## 6. Events vs polling (our polling design)

- Polling primitives — CONFIRMED (`conversations.history` page):
  - Cursor pagination: Quote: "Paginate through collections of data by setting the cursor parameter to a next_cursor attribute returned by a previous request's response_metadata." + `limit` "Maximum of 999. Default: 100" (internal apps; 15 for squeezed apps) + `oldest`/`latest`/`inclusive` semantics + `has_more`.
  - `ts` as per-message id within channel; threads via `conversations.replies` (parent `ts` + `latest_reply` change detection is our design — the `latest_reply` field itself was not re-quoted here: UNVERIFIED verbatim, confirm in L2S-04 from conversations.info/reference).
- Edits/deletions by polling: `message_changed` / `message_deleted` are Events-API event names (listed under scope "Compatible events"), NOT history fields — so a pure-history poller cannot see them directly. Our daily 7-day re-scan (compare `content_hash`, treat vanished `ts` as deleted) is the correct polling equivalent. Exact `edited` marker inside history payloads: UNVERIFIED in this pass (method reference shows message event shapes, not the edit flag name — pin in L2S-04 fixture capture).
- Ordering: chronological by `ts` (history returns portion of message events; request `oldest`/`latest` windows) — CONFIRMED in usage ("Call the method with no oldest or latest arguments to read the entire history").
- Verdict: polling + thread + re-scan is workable at Tier 3 rates for 10–30 mapped channels. HOLDS.

## 7. Permalinks

- Quote: "Easily exchange a message timestamp and a channel ID for a friendly HTTP-based permalink to that message. Handles message threads and all conversation types."
  - URL: https://docs.slack.dev/reference/methods/chat.getPermalink/ — 2026-10-04 — CONFIRMED
- Quote: "Fields: channel string Required ... message_ts string Required A message's ts value, uniquely identifying it within a channel" + example "{ \"ok\": true, \"channel\": \"C123ABC456\", \"permalink\": \"https://ghostbusters.slack.com/archives/C1H9RESGA/p135854651500008\" }" + thread variant with "?thread_ts=...&cid=..." — CONFIRMED.
- Building links without per-message API call (predictable `https://<team>.slack.com/archives/<C>/p<ts-no-dot>`): UNVERIFIED as supported contract (format is observable but not documented as stable; thread params complicate it). Recommendation: call `chat.getPermalink` once per evidence message and store it (Special tier, no scopes) — cheap at our volumes. Whether links open the macOS desktop app directly: UNVERIFIED (depends on Slack app URL handling; test in L2S-14).

## 8. Token handling (user token lifetime, revoke, test, leave/remove)

- User tokens: Quote: "User tokens represent workspace members. ... User token strings begin with xoxp-." — URL: https://docs.slack.dev/authentication/tokens.md — CONFIRMED. No documented expiry/rotation for `xoxp-` (contrast workflow tokens: "expire either 15 minutes after being issued" — same page — CONFIRMED as different type). Lifetime/rotation: UNVERIFIED (assume long-lived until revoke; re-check in L2S-03 against oauth.v2.access docs).
- Validation: `auth.test` — Quote: "This method checks authentication and tells 'you' who you are, even if you might be a bot." + "Scopes No scopes required" — URL: https://docs.slack.dev/reference/methods/auth.test — CONFIRMED. Failure markers `invalid_auth` / `token_revoked` / `token_expired` — CONFIRMED (same page error list).
- Revocation: `auth.revoke` — Quote: "This method revokes an access token. Use it when you no longer need a token." + "Scopes No scopes required" + success "{ \"ok\": true, \"revoked\": true }" — URL: https://docs.slack.dev/reference/methods/auth.revoke — CONFIRMED. Use on disconnect (L2S-03) alongside local Keychain delete.
- User leaves workspace / admin removes app: expected `token_revoked` / `invalid_auth` ("Authentication token is for a deleted user or workspace or the app has been removed when using a user token.") — CONFIRMED error text; exact trigger mapping UNVERIFIED until L2S-04 fake-server tests. Detection → `connection.status = revoked` + "reconnect" UX per existing connector contract.

## 9. Export fallback (per plan, who, JSON layout)

- Quote table: "Export data (messages and links to files*) from public channels | ✓ Free | ✓ Pro | ✓ Business+ | ✓ Enterprise Grid" / "Export data from public channels, private channels, and direct messages (DMs) | (not Free/Pro) | ✓ Business+ | ✓ Enterprise" + "Workspace Owners and Org Owners must apply to use these export types."
  - URL: http://slack.com/help/articles/201658943-Export-your-workspace-data — 2026-10-04 — CONFIRMED
- Quote: "Click Ready for download to access the zip file (it will contain message history in JSON format, and file links from all channels and conversations)."
- Layout — Quote: "Channels (channels.json) | ✓ | ✓ / Direct messages (dms.json) | | ✓ / Private channels (groups.json) | | ✓ / Group direct messages (mpims.json) | | ✓ / Members (users.json) / Folders for every public/private channel/DM / each channel or direct message will have its own folder ... organized by date in separate JSON files."
  - URL: https://slack.com/intl/en-au/help/articles/220556107-How-to-read-Slack-data-exports — 2026-10-04 — CONFIRMED
- Who: Workspace Owners/Admins for public; approved owners for all-channels; single-user/org variants Enterprise-only — CONFIRMED (same export page). Free-plan file-link 90-day limit + deleted->1yr caveats noted — CONFIRMED (import/export guide).
- Consequence: export fallback covers public channels for any pilot user, but private/DM backfill needs Business+ (with application) or Enterprise — onboarding must say so; API path remains primary.

## 10. Content shapes we must clean (L2S-05 input)

- Scope pages confirm `text` + attachments/blocks payloads exist, `subtype` bot/system messages exist (event names `bot_message`, `message_changed`, etc. listed as compatible events) — CONFIRMED at existence level. Exact mrkdwn grammar (`<@U...>`, `<#C...>`, links, emoji codes), `blocks` vs `text` precedence, file/canvas/huddle shapes: UNVERIFIED in this pass (formatting spec not fetched — read https://docs.slack.dev/reference/surfaces/formatting/ + message event reference during L2S-05 with fixtures). Cleaning plan (mrkdwn→text, mention→name, drop bots/system, files/canvases out of v1, huddle audio never) HOLDS as direction.

## 11. Consequences for T22–T25

| Plan assumption | Verdict | Change |
|---|---|---|
| T22 user-owned internal app, manifest link + pasted user token, no Firra-distributed app, no Events/Socket/relay in path | HOLDS WITH CHANGE | Keep. Add lawyer note on "templatized custom app" ToS line + no-training/AI-transfer wording before build. Manifest link pattern CONFIRMED. |
| T22 minimum scopes (history x4 + read x4 + users:read/email + permalink) | HOLDS | Freeze manifest to §4 list; no bot token, no write scopes. |
| T23 polling 5 min history + replies on `latest_reply` change + daily 7-day re-scan; export `.zip` fallback | HOLDS | Pin `edited`/delete-detection field names in L2S-04; export fallback primary only where API path blocked (Free private/DM, restricted app). |
| T24 minimization (mapped channels → client; DMs only with mapped-client people; internal opt-in; no calls/files/canvases/audio) | HOLDS | No ToS sentence against it; privacy policy must state it + retention. |
| T25 multi-channel signals (engagement across; latency per source; coverage guard; rule_version) | HOLDS | No API-rule dependency; needs `is_ext_shared`/`team_id` mapping (§5) + R14 channel-not-email rule. |
| Backfill 3 months of 20 channels via API | HOLDS (internal Tier 3) | Would be BROKEN as a distributed app (Tier 1) — never distribute. |
| Non-admin pilot user | HOLDS WITH CHANGE | Works iff approval off or owner approves; otherwise export-only. Onboarding must show: (1) "ask your Workspace Owner to approve <app> (scopes list)", (2) where (Admin → Apps and workflows → App Management Settings), (3) client-ID/scope evidence for the admin, (4) export fallback path. |

BROKEN items: none (no rule found forbidding the plan). Two lawyer-confirm items at top of Report.

## What we still do not know (cheapest settle path each)

1. Manifest-template distribution reading of ToS "templatized one-off custom apps" → lawyer + one Slack support question with exact flow. Free.
2. User-directed AI transfer (Slack → user-chosen CLI, no training, consent) permissibility → same lawyer pass as Google L0-04; verification-form analogue is ToS compliance review. Free.
3. `latest_reply` field name + `edited` marker + `ts` uniqueness edge cases → L2S-04 reference read + recorded fixtures (no API calls). Free.
4. Permalink-without-API-call stability + macOS desktop-app opening → L2S-14 live test on test workspace. Free.
5. `xoxp-` rotation/expiry policy → oauth.v2.access docs in L2S-03. Free.
6. Free-workspace app-creation/approval deltas → create Free test workspace in L2S-14 and record. Free.
7. Exact blocked/request-approval user-visible copy → screenshot in pilot. Free.
8. mrkdwn/blocks/subtype/file shapes → formatting + message-event references in L2S-05 with fixtures. Free.
