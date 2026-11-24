# Research S1: Google rules for a local-only Gmail and Calendar app (L0-04)

Date read: 2026-10-04 (all URLs below). Tags: CONFIRMED = primary Google source says it verbatim. UNVERIFIED = inferred, secondary, or no primary source found.

Architecture under review: native macOS app, Desktop OAuth client, system browser, loopback redirect `http://127.0.0.1:<port>`, PKCE, refresh tokens in Keychain. Scopes: `gmail.readonly`, `calendar.readonly` (events only), `openid email`. Mail stored only on Mac. Extraction sends mail text to a user-chosen CLI AI on the same Mac under the user's own account. Firra cloud never receives Gmail data (sign-in, MCP relay of derived facts only, updates).

## 1. Restricted scopes

**Answer: `gmail.readonly` is restricted. `calendar.readonly` is sensitive (not restricted). `openid email` is non-sensitive.**

- Quote: "`https://www.googleapis.com/auth/gmail.readonly` | View your email messages and settings." under heading "Restricted scopes"
  - URL: https://developers.google.com/workspace/gmail/api/auth/scopes
  - Date: 2026-10-04 — CONFIRMED
- Quote: "Restricted : These scopes provide wide access to Google user data and require restricted scope OAuth App Verification . For more information, see Google API Services User Data Policy and Additional Requirements for Specific API Scopes ."
  - URL: https://developers.google.com/workspace/gmail/api/auth/scopes
  - Date: 2026-10-04 — CONFIRMED
- Quote: "Sensitive, calendar.readonly, See and download any calendar you can access using Google Calendar."
  - URL: https://developer.nylas.com/docs/provider-guides/google/google-verification-security-assessment-guide (secondary, summarizing Google classification)
  - Date: 2026-10-04 — UNVERIFIED (need Google Cloud Console Data Access page screenshot or https://developers.google.com/identity/protocols/oauth2/scopes listing; Calendar auth page itself does not label sensitivity)
- Calendar scope meaning, CONFIRMED primary:
  - Quote: "`https://www.googleapis.com/auth/calendar.readonly` | See and download any calendar you can access using your Calendar."
  - URL: https://developers.google.com/workspace/calendar/api/auth
  - Date: 2026-10-04 — CONFIRMED (meaning; sensitivity tag UNVERIFIED, see above)
- Narrower alternative noted (UNVERIFIED whether Google accepts it for our use): `calendar.events.readonly` = "View events on all your calendars." (same URL above, CONFIRMED meaning). Sensitivity of `calendar.events.readonly` is UNVERIFIED.

Implication: one restricted scope (`gmail.readonly`) triggers the restricted-scope verification + CASA track. Calendar adds only sensitive-scope verification.

## 2. Security assessment (CASA) — when required, on-device exemption

**Answer: required when the app "accesses restricted scope Google users' data from or through a third-party server". Pure on-device processing with no transmission to a developer server is the documented exemption path, but the exact boundary for (a) derived facts to our relay and (b) user-directed transmission to a user-chosen AI provider is UNVERIFIED.**

Primary quotes:

- Quote: "Every app that requests access to Google users' restricted data and has the ability to access data from or through a third-party server must go through a security assessment from Google-empanelled security assessors."
  - URL: https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification
  - Date: 2026-10-04 — CONFIRMED
- Quote: "If you store restricted scope data on servers (or transmit), then you must go through a security assessment."
  - URL: https://developers.google.cn/workspace/gmail/api/auth/scopes?hl=en (footnote under scope tables, same text on .com page)
  - Date: 2026-10-04 — CONFIRMED
- Quote: "To maintain the security of Google users' data, applications requesting access to restricted scopes must undergo an annual security assessment."
  - URL: https://support.google.com/cloud/answer/13465431?hl=en
  - Date: 2026-10-04 — CONFIRMED (unqualified wording; the qualified wording above controls scope)
- Quote: "Ensure your app complies with the Additional requirements for specific API scopes , which includes undergoing an annual security assessment if your app accesses restricted scope Google users' data from or through a third-party server."
  - URL: https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification (step 12)
  - Date: 2026-10-04 — CONFIRMED
- Quote: "Apps that access restricted scopes are required to complete a security assessment every 12 months. This 12-month period is calculated from the effective date of the app's previous Letter of Validation (LOV)."
  - URL: https://support.google.com/cloud/answer/13463816?hl=en
  - Date: 2026-10-04 — CONFIRMED (annual recertification)

On-device exemption (secondary, UNVERIFIED for our facts):

- Quote: "Local Data Storage: Local client applications don't need to undergo a security assessment because data is run, stored, and processed only on the user's device. Local client applications that only allow user-configured transmissions of Restricted Scope data from the device may be exempt from this requirement."
  - URL: https://groups.google.com/g/google-apps-script-community/c/BX_SFP6Q82E (quoting https://support.google.com/cloud/answer/9110914 FAQ, 2021 snapshot)
  - Date: 2026-10-04 — UNVERIFIED (forum quote of old FAQ text; current FAQ page fetched 2026-10-04 no longer shows this paragraph verbatim, so treat as stale until re-confirmed via verification form)
- Counter-signal that CASA may still apply even to local-only restricted-scope apps (forum, not Google):
  - Quote: "Yes, a CASA assessment may still be required depending on how your software handles the user data, even if it is not transmitted to external servers other than Google's."
  - URL: https://discuss.google.dev/t/is-casa-required-for-all-access-restricted-scopes/340650
  - Date: 2026-10-04 — UNVERIFIED (community answer, not Google staff)
- Directly on-point unanswered question (shows Google has not publicly settled derived-data boundary):
  - Quote: "Does a derived, structured list (Variant A) count as 'restricted scope data stored on servers', or only raw mail content / headers / tokens?"
  - URL: https://discuss.google.dev/t/gmail-restricted-scope-is-the-casa-assessment-required-when-mail-is-processed-on-device-and-only-a-derived-subscription-list-reaches-our-server/398372
  - Date: 2026-10-04 — UNVERIFIED (question from 2026-09-19, no Google answer at time of reading)

What counts as "transmitting to a server":

- No primary definition found beyond "from or through a third-party server" / "store ... on servers (or transmit)". Whether a user-configured transmission to a user-chosen AI provider (Claude Code / Codex using the user's own subscription, invoked locally) counts as a prohibited "transfer" vs an allowed user-consented feature use is UNVERIFIED — see §3 Limited Use transfer exception, which allows transfers "To provide or improve your appropriate access or user-facing features ... and only with the user's consent".
- Cheapest way to settle: ask Google through the OAuth Verification Center / verification form with the exact architecture diagram (Desktop client, no Firra server receipt, relay carries only derived facts, AI invocation local under user's account). Keep the ticket ID in the Report.

## 3. Limited Use

Source: Google API Services User Data Policy, last updated 2024-02-15. URL: https://developers.google.com/terms/api-services-user-data-policy — Date: 2026-10-04 — all quotes below CONFIRMED as policy text.

- Storing / use limitation:
  - Quote: "Limit your use of data to providing or improving user-facing features that are prominent in the requesting application's user interface;"
- Transfers:
  - Quote: "Transfers of data are not allowed, except:"
  - Quote: "To provide or improve your appropriate access or user-facing features that are visible and prominent in the requesting application's user interface and only with the user's consent;"
  - Quote: "For security purposes (for example, investigating abuse);"
  - Quote: "To comply with applicable laws; or,"
  - Quote: "As part of a merger, acquisition, or sale of assets of the developer after obtaining explicit prior consent from the user."
  - Prohibited: Quote: "Transferring or selling user data to third parties like advertising platforms, data brokers, or any information resellers." / Quote: "Transferring, selling, or using user data for serving ads, including retargeting, personalized or interest-based advertising." / Quote: "Transferring, selling, or using user data to determine credit-worthiness or for lending purposes."
- Humans reading:
  - Quote: "Don't allow humans to read the data, unless:"
  - Quote: "You first obtained the user's affirmative agreement to view specific messages, files, or other data, with the limited exception of use cases approved by Google under additional terms applicable to the Nest Device Access program;"
  - Quote: "It is necessary for security purposes (for example, investigating a bug or abuse);"
  - Quote: "It is necessary to comply with applicable law; or"
  - Quote: "The data (including derivations) is aggregated and used for internal operations in accordance with applicable privacy and other jurisdictional legal requirements."
- AI/ML and training:
  - The User Data Policy page itself does not contain the words "train" or "AI model" in the Limited Use block; the AI-training prohibition lives in product-specific Workspace policy messaging. Closest primary CONFIRMED statement found:
  - Quote: "Our Limitation on User Data Transfer prohibits the use of Workspace user data to train non-personalized AI and/or ML models."
  - URL: https://workspace.google.com/blog/ai-and-machine-learning/api-policy-protections
  - Date: 2026-10-04 — CONFIRMED (blog, official Workspace policy account)
  - What the policy requires us to state: UNVERIFIED at primary-source level for CLI-subscription routing. Secondary example of compliant wording (use.ai privacy policy) shows the pattern Google expects (no training, API/commercial terms only, user-initiated action), but it is not Google's text:
  - Quote: "We do not use Google user data — including any data obtained from Google Workspace APIs — to develop, improve, or train generalized or non-personalized AI and/or machine learning models, even in de-identified or anonymized form."
  - URL: https://use.ai/privacy — Date: 2026-10-04 — UNVERIFIED (third-party wording, useful template only)
- Privacy policy must say:
  - Quote: "You must publish a privacy policy that fully documents how your application interacts with user data."
  - Quote: "Your use of Google user data must be limited to the practices explicitly disclosed in your published privacy policy"
  - Quote: "The privacy policy must disclose the manner in which your application accesses, uses, stores, or shares Google user data. The privacy policy must comply with the Google API Services User Data Policy and the Limited Use requirements for restricted scopes."
  - URL for third quote: https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification — Date: 2026-10-04 — CONFIRMED
  - Practical consequence: the policy must name each scope, what is accessed, that mail stays on-device, what (if anything) is transmitted to whom (relay derived facts, user-chosen AI), retention/deletion, human-access conditions, and no training/ads/sale. Exact reviewer-accepted phrasing is UNVERIFIED until submission.

Open hole for Firra: sending mail text to a user-chosen CLI AI under the user's own consumer subscription is (a) a "transfer" the policy presumptively forbids except for user-facing features with consent, and (b) potentially processed under consumer terms that may permit training. Whether Google treats user-directed, on-device, consent-based routing to the user's own AI as an allowed transfer is UNVERIFIED and is the #1 question for the verification form. Mitigation in plan (no Firra server receipt, explicit consent, API/commercial terms preference) aligns with the exception text but has no primary-source blessing yet.

## 4. Verification (Testing → Production, Desktop app)

- Brand + Data Access verification required for sensitive/restricted before any Google Account beyond test users can grant access without caps. CONFIRMED:
  - Quote: "If your public application uses scopes that permit access to certain user data, it must complete a verification process. If you see unverified app on the screen when testing your application, you must submit a verification request to remove it."
  - URL: https://developers.google.com/identity/protocols/oauth2/native-app — Date: 2026-10-04 — CONFIRMED
- What is needed (Desktop/In production path):
  - Verified domain, homepage, privacy policy URL, branding review, demo video showing each scope in use, per-scope justification text. The scope-declaration + privacy-policy-link requirements are CONFIRMED (see §3 quotes). Demo-video and justification specifics are UNVERIFIED at primary-source level in this pass (widely described in secondary playbooks; Google's Verification Center asks for them per-scope but the exact field list was not re-fetched here — settle via console during L2-01).
- How long (Google's own statements only):
  - Quote: "The sensitive scope verification process typically takes 3-5 business days to complete."
  - URL: https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification — Date: 2026-10-04 — CONFIRMED
  - Quote: "Restricted scope verification is a much more involved process. ... Restricted Scope Verification (4-6 weeks)"
  - URL: https://developers.googleblog.com/get-smart-about-preparing-your-app-for-oauth-verification (2019-09-18, official Google Developers Blog) — Date: 2026-10-04 — CONFIRMED as Google's statement (old; still the only Google-published duration found; treat current queue time as UNVERIFIED)
  - Quote: "For this reason, this restricted scopes verification process can potentially take several weeks to complete."
  - URL: https://support.google.com/cloud/answer/9110914 (via gist mirror quoting FAQ; primary FAQ fetch 2026-10-04 confirms multi-week CASA track indirectly) — Date: 2026-10-04 — UNVERIFIED (mirror quote; use blog + verification pages as primary)
- What can ship before verification:
  - Testing status: only allowlisted test users, warning UI, 7-day refresh expiry (§5). CONFIRMED.
  - In production + unverified: any Google user can start OAuth but sees the "unverified app" interstitial and a ~100-user cap applies. CONFIRMED:
  - Quote: "Published | External | No | Unverified | Any Google user can access. Strongly discouraged. ... for apps requesting sensitive or restricted scopes, unverified app warnings (Danger UI) will be displayed to users, and a hard cap of 100 total users applies."
  - URL: https://developers.google.com/identity/protocols/oauth2/production-readiness/overview — Date: 2026-10-04 — CONFIRMED
  - Practically: pilot with 2–3 partners can run on Testing or In-production-unverified; public launch cannot.

## 5. Testing mode limits

- Quote: "Only users explicitly added to the test user allowlist can access the app (limited to a hard cap of 100 test users)."
  - URL: https://developers.google.com/identity/protocols/oauth2/production-readiness/overview — Date: 2026-10-04 — CONFIRMED
- Quote: "A Google Cloud Platform project with an OAuth consent screen configured for an external user type and a publishing status of Testing is issued a refresh token expiring in 7 days, unless the only OAuth scopes requested are a subset of name, email address, and user profile (through the userinfo.email, userinfo.profile, openid scopes, or their OpenID Connect equivalents)."
  - URL: https://developers.google.com/identity/protocols/oauth2 — Date: 2026-10-04 — CONFIRMED
  - Applies to Desktop client type and to sensitive/restricted scopes: CONFIRMED by the same sentence (no Desktop exemption stated; our scopes are outside the basic-identity exception, so the 7-day rule applies in Testing).
- Quote: "Users see a warning UI indicating the app is in testing, rather than the standard unverified app screen."
  - URL: https://developers.google.com/identity/protocols/oauth2/production-readiness/overview — Date: 2026-10-04 — CONFIRMED
- Additional token-expiry causes (CONFIRMED, same OAuth page): user revokes, unused 6 months, password change with Gmail scopes, max live refresh tokens per account per client (100, oldest invalidated without warning). Quote: "There is currently a limit of 100 refresh tokens per Google Account per OAuth 2.0 client ID. If the limit is reached, creating a new refresh token automatically invalidates the oldest refresh token without warning."

## 6. Desktop OAuth details

Source: https://developers.google.com/identity/protocols/oauth2/native-app — Date: 2026-10-04 — all CONFIRMED unless noted.

- Client secret:
  - Quote: "`client_secret` **Optional** The client secret obtained from the Cloud Console Clients page."
  - And: Quote: "(The `client_secret` is not applicable to requests from clients registered as Android, iOS, or Chrome applications.)" (refresh-token section)
  - Interpretation: for Desktop clients the secret field exists but is optional / not confidential. Installed apps "cannot keep secrets". CONFIRMED. Practice: do not embed a secret; use PKCE without secret. Whether Google issues a secret for Desktop clients at all is UNVERIFIED (console behavior; check during L2-01).
- Loopback redirects:
  - Quote: "`http://127.0.0.1:port` or `http://[::1]:port`" under "Loopback IP address (macOS, Linux, Windows desktop)"
  - Quote: "Set the application type to **Desktop app**." / "Recommended usage: macOS, Linux, and Windows desktop (but not Universal Windows Platform) apps"
  - Quote: "Note that support for the loopback IP address redirect option on **mobile apps** is DEPRECATED."
  - OOB (manual copy/paste) is dead: Quote: "The manual copy/paste option, also referred to as an out of band (OOB) redirect method, is no longer supported." CONFIRMED.
- PKCE:
  - Quote: "`code_challenge` **Recommended** Specifies an encoded `code_verifier`..." / "`code_challenge_method` **Recommended** ... The only supported values for this parameter are `S256` or `plain`."
  - So: recommended, not "required", with S256 recommended for the challenge itself. CONFIRMED. (Plan should still always send S256 + `state`; UNVERIFIED whether reviewers now mandate it.)
- Revocation + detection:
  - Quote: "To programmatically revoke a token, your application makes a request to `https://oauth2.googleapis.com/revoke` and includes the token as a parameter"
  - Quote: "If the token is an access token and it has a corresponding refresh token, the refresh token will also be revoked."
  - Detection: expired/invalid refresh yields `invalid_grant`. Quote: "[When refreshing an access token](#offline), the token may have expired or has been invalidated. Authenticate the user again and ask for user consent to obtain new tokens."
  - Also: Quote: "The user has revoked your app's access ." listed as a refresh-token expiry cause. All CONFIRMED.
- Must open system browser (not embedded webview): `disallowed_useragent` error documented for WKWebView; developers "should instead use ... AppAuth for iOS" / system browser. CONFIRMED (error table on same page).

## 7. Gmail / Calendar API behaviour we depend on

### Gmail history

- Quote: "Supplying an invalid or out of date startHistoryId typically returns an HTTP 404 error code. A historyId is typically valid for at least a week, but in some rare circumstances may be valid for only a few hours. If you receive an HTTP 404 error response, your application should perform a full sync."
  - URL: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list — Date: 2026-10-04 — CONFIRMED
- Quote: "History results are returned in chronological order (increasing historyId )." / "Maximum number of history records to return. This field defaults to 100. The maximum allowed value for this field is 500."
  - Same URL — CONFIRMED

### Gmail quotas and per-method costs

Source: https://developers.google.com/workspace/gmail/api/reference/quota — Date: 2026-10-04 — CONFIRMED:

- Quote: "Per minute per project | 1,200,000 quota units"
- Quote: "Per minute per user per project | 6,000 quota units"
- Quote: "Per day per project | 80,000,000 quota units" (daily billing threshold; "Google will share full billing details later in 2026 with at least 90 days' notice before any changes take effect." / "You can't request an increase on this daily threshold limit.")
- Per-method costs: Quote table rows: "history.list | 2", "messages.list | 5", "messages.get | 20", "messages.attachments.get | 20", "messages.import | 25", "threads.list | 10". CONFIRMED.
- Practical math (derived, UNVERIFIED as Google statement but arithmetic from CONFIRMED numbers): at 6,000 units/min/user, ~1,200 `messages.list` (5 units) or 300 `messages.get` (20 units) per user per minute burst; backfill of 3 months must be paced and use `history.list` (2 units) for incremental.

### messages.get format and size

- `format=raw` vs `full` exact semantics and size limits: UNVERIFIED in this pass (messages.get reference fetch truncated; did not capture verbatim format definitions). Do not rely on memory. Settle via https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get during L2-02. Known secondary constraint to verify: 25 MB mail size / attachment limits are sending-path numbers, not read-path guarantees.

### messages.list pagination

- Pagination via `pageToken`/`maxResults` is standard; exact defaults/max for messages.list were not captured verbatim in this pass: UNVERIFIED. Settle via messages.list reference during L2-02.

### Calendar syncToken

- Quote: "If the syncToken expires, the server will respond with a 410 GONE response code and the client should clear its storage and perform a full synchronization without any syncToken ."
  - URL: https://developers.google.com/workspace/calendar/api/v3/reference/calendarList/list (identical text on events.list, settings.list, and https://developers.google.com/workspace/calendar/api/guides/sync) — Date: 2026-10-04 — CONFIRMED
- Quote: "Sometimes sync tokens are invalidated by the server, for various reasons including token expiration or changes in related ACLs. In such cases, the server will respond to an incremental request with a response code 410 . This should trigger a full wipe of the client's store and a new full sync."
  - URL: https://developers.google.com/workspace/calendar/api/guides/sync — Date: 2026-10-04 — CONFIRMED
- How long a syncToken lives before 410: UNVERIFIED (no duration published; plan for arbitrary invalidation).

## 8. Google Workspace admin controls (pilot runbook)

- Admins can block any third-party OAuth app, including Testing/Published, Verified/Unverified. CONFIRMED:
  - Quote: "Google Workspace administrators can always Block any OAuth app, whether it is Internal or External, Testing or Published, or Verified or Unverified, preventing their users from authorizing it."
  - URL: https://developers.google.com/identity/protocols/oauth2/production-readiness/overview — Date: 2026-10-04 — CONFIRMED
  - Quote: "**Blocked** —Can't access any Google service." / "**Limited** —Can access only unrestricted Google services." / "**Trusted** —Can access all Google services (both restricted and unrestricted)."
  - URL: https://knowledge.workspace.google.com/admin/apps/control-which-apps-access-google-workspace-data — Date: 2026-10-04 — CONFIRMED
- What the user sees: authorization fails with `admin_policy_enforced`. CONFIRMED:
  - Quote: "`admin_policy_enforced` The Google Account is unable to authorize one or more scopes requested due to the policies of their Google Workspace administrator. See the Google Workspace Admin help article Control which third-party & internal apps access Google Workspace data for more information"
  - URL: https://developers.google.com/identity/protocols/oauth2/native-app (Errors section) — Date: 2026-10-04 — CONFIRMED
  - Exact end-user string in the browser ("blocked by admin", request-access flow) is UNVERIFIED — capture a screenshot during pilot.
- Runbook consequence: pilot instructions must include the OAuth client ID and the Admin console path (Security > Access and data control > API controls > Manage Third-Party App Access → Trusted/Limited) so a Workspace admin can allowlist Firra. CONFIRMED path from Admin help above. Also note: Quote: "When a user attempts to access an unconfigured third-party app, access is blocked by default." applies to Education contexts / restrictive defaults — URL: https://knowledge.workspace.google.com/admin/apps/review-and-manage-third-party-app-access-requests — Date: 2026-10-04 — CONFIRMED as written (scope of "by default" varies by admin setting; confirm per-partner).

## 9. Consequences for us

| Plan decision | CASA | Verification | Privacy policy | Verdict |
|---|---|---|---|---|
| No Firra server receives Gmail data; tokens stay in Keychain; sync from the Mac | Strongest available argument for CASA exemption under "from or through a third-party server" wording, but exemption text is stale/secondary and derived-facts boundary unanswered | Still needs restricted-scope verification (gmail.readonly) for any user beyond test allowlist; Testing (100 users, 7-day refresh) suffices for pilot | Must still disclose access/use/storage/sharing per User Data Policy even if all local | HOLDS WITH CHANGE: add explicit "what never leaves the Mac" + relay allowlist to policy; file verification question early |
| Derived facts only through MCP relay (no bodies) | UNVERIFIED whether derived facts = "restricted scope data stored on servers". If Google says yes, CASA applies even with no bodies | Same as above | Must list relay as recipient, fields, purpose, retention | HOLE UNTIL SETTLED: get written classification before F7 relay work |
| Mail text to user-chosen CLI AI on same Mac under user's own account | UNVERIFIED: fits Limited Use transfer exception text ("user-facing features ... only with the user's consent") but no primary blessing; consumer-plan training risk | Must be in scope justification + demo video + policy | Must name AI routing, consent moment, no-training expectation, deletion | HOLE UNTIL SETTLED: primary risk R2; if forbidden, plan falls back to in-conversation processing only |
| Desktop client + loopback + PKCE, `gmail.readonly` + `calendar.readonly`/`calendar.events.readonly` + `openid email` | n/a (reduces scope surface; narrowest-scope rule) | Narrowest scope helps review; prefer `calendar.events.readonly` over `calendar.readonly` if events-only suffices (UNVERIFIED reviewer preference) | List each scope + why | HOLDS: implement exact §6 details |
| Polling history API every 5 min; full resync on 404; Calendar full sync on 410 | n/a | n/a | n/a | HOLDS (quotas support single-user polling; backfill math in L2-02) |
| Workspace pilot users | n/a | Unverified/Testing apps face admin block; need allowlist instructions | n/a | HOLDS WITH CHANGE: runbook must carry client ID + admin steps |

Plan holes stated plainly:

1. CASA exemption for pure on-device + derived-facts relay is UNVERIFIED (stale FAQ quote vs unqualified Help Center wording + unanswered 2026-09 forum thread).
2. User-directed AI transfer under Limited Use + consumer subscription training is UNVERIFIED and is the design-critical question (Stop-and-ask trigger: if Google forbids it, architecture changes to in-conversation only).
3. Exact `format=raw` vs `full`, messages.list pagination defaults, syncToken lifetime, Desktop secret issuance, and current restricted-review queue time are UNVERIFIED implementation details for L2-01/L2-02 to pin from references + console.

## What we still do not know (cheapest way to settle each)

1. CASA exemption for our exact diagram → ask via OAuth Verification Center / verification form; keep ticket ID. Cost: days, free. (Blocks F7 relay scope.)
2. Derived structured list = "restricted scope data"? → same ticket, explicit example payload. Free.
3. User-chosen CLI AI routing allowed under Limited Use? → same ticket + quote our consent + no-training wording. If "no", fallback to L3-04 only. Free.
4. `calendar.readonly` vs `calendar.events.readonly` sensitivity + reviewer preference → Cloud Console Data Access page classification. Free, minutes.
5. messages.get formats, messages.list pagination, size caps → read the two reference pages in L2-02. Free.
6. Current restricted verification + CASA queue time and cost (assessor quote) → verification submission + CASA assessor quote (TAC Security public pricing page is secondary; get written quote). Cost: assessor fee if CASA required.
7. Exact blocked-by-admin user-visible string → screenshot in pilot with a Workspace test user. Free.
8. Whether Desktop clients are issued a secret at all → check Cloud Console Clients page during L2-01. Free.
