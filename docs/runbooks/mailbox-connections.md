# Mailbox connections (Gmail, Google Calendar, Outlook) — staging setup

> **Local v1:** on the Mac, Gmail/Google Calendar connect via Desktop OAuth (PKCE, loopback) with tokens
> in the Keychain, Slack via a pasted user token from the owner's own app, all syncing by polling in the
> engine — see `docs/connectors.md` "Local v1 rows". The Terraform / Pub/Sub / Graph-subscription flow
> below is the legacy staging setup and stays only until phase F9.

This turns on the P1 connect flows in staging: the Google OAuth client for Gmail + Google Calendar,
the Microsoft Entra app for Outlook mail + calendar, and push notifications for both. It assumes
`docs/runbooks/staging-setup.md` is done (values **A** project id, **P** api URL, **W** web URL).

Without these steps the app still works: the connect buttons answer "not set up yet".

Order matters for every provider: **create the secret shell → add its value → wire it into the
services**. A Cloud Run revision that references a secret without a version fails to start.

```bash
# Same helper as staging-setup.md step 4 (reads the value without echoing it).
add_secret() {
  local v
  read -rs "v?Value for $1: "; echo
  printf '%s' "$v" | gcloud secrets versions add "$1" --data-file=- --project "$PROJECT_ID"
}
```

---

## 1. Google OAuth client (Gmail + Calendar, read-only)

`gmail.readonly` is a **restricted** scope: until Google verification + CASA (a P9 launch gate)
the app stays in **Testing** mode, which allows up to 100 test users you list by hand.

1. https://console.cloud.google.com/auth/overview?project=<A> (Google Auth Platform). If you
   already configured it for Google sign-in (staging-setup step 6.2), keep that configuration.
   - **Audience:** External, publishing status **Testing**. **Test users → Add users:** every
     Google address that will connect a mailbox (owner, contributors, test accounts).
   - **Data access → Add or remove scopes:** add
     `https://www.googleapis.com/auth/gmail.readonly` and
     `https://www.googleapis.com/auth/calendar.readonly` (plus `openid`, `email`) → **Update** →
     **Save**.
2. https://console.cloud.google.com/auth/clients?project=<A> → **Create client**:
   - Application type **Web application**, name `firra-api-staging`.
   - **Authorized redirect URIs → Add URI:** `<P>/oauth/google/callback`.
   - **Create**, then copy the **Client ID** and **Client secret** (password manager).
3. Terraform, secret shell only (the services are not changed yet):

   ```bash
   cd infra/terraform/envs/staging
   # terraform.tfvars:  enable_google = true   google_client_id = "<client id>"
   terraform apply -target='module.env.google_secret_manager_secret.app'
   add_secret google-oauth-client-secret        # paste the client secret
   terraform apply                              # wires api + worker, creates the Gmail push topic
   ```

   The full apply also creates the `gmail-push` Pub/Sub topic (EU-only storage), grants Gmail's
   publisher `gmail-api-push@system.gserviceaccount.com` publish rights, and pushes to
   `<P>/webhooks/gmail` with an OIDC token of `firra-pubsub-push@<A>.iam.gserviceaccount.com`.
   If apply fails on the publisher grant with a "domain restricted sharing" error, your
   organization policy blocks outside accounts: add an exception for that account (Organization
   policies → `iam.allowedPolicyMemberDomains`) or leave push off — the 15-minute sweep still
   syncs.

**Check**

```bash
gcloud run services describe firra-api --region=europe-west3 --project "$PROJECT_ID" \
  --format='value(spec.template.spec.containers[0].env)' | tr ';' '\n' | grep -E 'GOOGLE_CLIENT_ID|GMAIL_PUSH'
gcloud pubsub subscriptions describe gmail-push-api --project "$PROJECT_ID" --format='value(pushConfig.pushEndpoint)'
```

Then in the app: **Connections → Connect your Gmail** with a test user → Google consent →
back on Connections with "Mailbox connected" and the import progress. After the first
incremental sync (≤ 15 minutes, or right after connect), new mail arrives within about a minute.

---

## 2. Microsoft Entra app (Outlook mail + calendar)

Work or school accounts only (`MICROSOFT_TENANT = organizations`). For testing without a company
tenant, join the Microsoft 365 Developer Program (free sandbox tenant with sample mailboxes).
Publisher verification is a P9 launch gate; before it, users of other tenants see an "unverified"
warning and their admin may have to approve the app.

1. https://entra.microsoft.com → **Entra ID → App registrations → New registration**:
   - Name `Firra Outlook (staging)`.
   - Supported account types: **Accounts in any organizational directory (Any Microsoft Entra ID
     tenant – Multitenant)**.
   - Redirect URI: platform **Web**, `<P>/oauth/microsoft/callback`.
   - **Register**, then copy **Application (client) ID** from **Overview**.
   - **Authentication → Web → Redirect URIs → Add URI:** `<P>/oauth/microsoft/admin-callback`
     (admin consent for organization-wide access) → **Save**.
2. **API permissions → Add a permission → Microsoft Graph**:
   - **Delegated permissions:** `openid`, `email`, `profile`, `offline_access`, `Mail.Read`,
     `Calendars.Read` → **Add permissions**.
   - **Application permissions** (only for org-wide mode, used later by the admin setup UI):
     `Mail.Read`, `Calendars.Read`. These need admin consent in each customer tenant; they are
     unused until an owner sets up org-wide access.
3. **Certificates & secrets → Client secrets → New client secret** (description `staging`,
   expiry 12 months; put a reminder in your calendar) → copy the **Value** immediately.
4. Terraform:

   ```bash
   cd infra/terraform/envs/staging
   # terraform.tfvars:  enable_microsoft = true   microsoft_client_id = "<application id>"
   terraform apply -target='module.env.google_secret_manager_secret.app'
   add_secret microsoft-oauth-client-secret     # paste the secret value
   terraform apply                              # wires api + worker and the Graph notification URL
   ```

**Check**

In the app: **Connections → Connect your Outlook (Microsoft 365)** → Microsoft sign-in and
consent → back on Connections with two rows (Outlook, Outlook Calendar) and import progress.
After the first incremental sync, the worker creates Graph subscriptions; Graph validates
`<P>/webhooks/microsoft` during creation. Verify in the logs:

```bash
gcloud logging read 'resource.labels.service_name="firra-api" AND jsonPayload.route="/webhooks/microsoft"' \
  --project "$PROJECT_ID" --limit 5 --format='value(jsonPayload.status)'   # 200 (validation), then 202s
```

---

## 3. Organization-wide access (optional)

In the app: **Connections → Connect mailboxes for your whole team**.

- **Google Workspace:** the page shows the worker service account's client ID and the two scopes.
  A super admin adds them at https://admin.google.com → Security → Access and data control →
  API controls → **Manage domain-wide delegation → Add new**. Then list the mailboxes on the page.
- **Microsoft 365:** first connect your own Outlook mailbox (Firra takes your organization's
  tenant from that verified sign-in and refuses consent for any other tenant). Click **Grant admin
  consent** (a Microsoft 365 admin must approve), then list the mailboxes by sign-in address.
  Restrict which mailboxes the app may read with an Exchange application access policy or RBAC for
  Applications in your tenant.

Every mailbox added this way is recorded in the audit log (`connection.org_added`).

## 4. Contributors

Team members connect from their connect link (**Team sources → Create link**). The link page offers
both Google and Microsoft. In Google Testing mode, add each contributor's address as a test user
first (step 1.1), or their consent fails with "access blocked".

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `connect_error=google_not_configured` / `microsoft_not_configured` | Client id or secret not wired: re-run step 1.3 / 2.4, check the env on `firra-api`. |
| Google "Access blocked: app has not completed verification" | The address is not a test user (step 1.1). |
| `connect_error=no_refresh_token` (Google) | The account had consented before without offline access: remove Firra at https://myaccount.google.com/permissions and connect again. |
| `connect_error=no_mailbox` (Microsoft) | The account has no Exchange Online mailbox (license). |
| Microsoft "Need admin approval" | The customer tenant blocks user consent: their admin grants consent for the app once. |
| Connection shows "Access was revoked" | The user or admin revoked consent, or the refresh token expired: connect again. |
| Import stays below 12 of 12 months, worker logs `task.failed` with `error_class=ProviderError` | `provider_status`/`provider_reason` on the log entry name the cause. Throttling (403 `rateLimitExceeded`) is retried with backoff, at most two months per mailbox run at once, and a still-throttled month waits 5 minutes (`task.continued`, `delay_seconds=300`) instead of failing. Gmail's per-user limit is `totalQueryCostPerMinutePerUser` (Cloud Console → APIs → Gmail API → Quotas); a month stuck in "queued" is re-dispatched by the sweep within about 45 minutes. Connect the same mailbox again: months that failed are re-run from their saved page; finished months are not repeated. |
