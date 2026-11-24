# Staging setup — step by step

This guide takes you from nothing to a working staging deploy. It covers the seven manual setup
steps, then the first deploy and the P0 acceptance check. Do the steps in order; each ends with a
**Check** so you know it worked before moving on.

- Everything stays in the EU: GCP region `europe-west3` (Frankfurt), Supabase region
  `eu-central-1` (Central EU, Frankfurt).
- Commands are for **zsh on macOS** and are run from the repository root unless stated otherwise.
- `<…>` means "replace with your value". Dashboard labels can shift slightly between releases; the
  direct links are the most reliable way to get to the right page.
- **Never** paste secret values into files in the repo, tickets, chat, or shell commands that end
  up in history. This guide shows how to enter them safely.

## Before you start

| Need | How |
|---|---|
| Google account with a payment method | Used for the GCP billing account (new accounts get free-trial credits) |
| Supabase account | https://supabase.com/dashboard (sign in with GitHub is fine) |
| GitHub access to `malikibric/fira` | Admin rights (to create environments and secrets) |
| A password manager | To store the Supabase database password and keys |
| Tools | `gcloud`, `terraform`, `jq`, `gh` (logged in), `pnpm` — check with the command below |

```bash
gcloud --version | head -1 && terraform version | head -1 && jq --version && gh auth status && pnpm --version
```

### Values worksheet

You will collect these values along the way. Keep them in your password manager (the ones marked
**secret**) or a scratch note (the rest). The last column says where each value ends up.

| # | Value | Example / format | Secret? | Comes from | Goes to |
|---|---|---|---|---|---|
| A | GCP project id | `firra-staging-7k2p` | no | Step 1.3 (you choose) | Terraform, GitHub var `GCP_PROJECT_ID` |
| B | Billing account id | `01AB23-45CD67-89EF01` | no | Step 1.1 | Terraform |
| C | Billing currency | `USD` / `EUR` | no | Step 1.1 | Terraform `budget_currency` |
| D | Supabase database password | long random string | **secret** | Step 2.2 | Inside value H only |
| E | Supabase project ref | `abcdefghijklmnopqrst` | no | Step 2.3 | Builds the URLs below |
| F | Supabase project URL | `https://<E>.supabase.co` | no | Step 2.3 | Terraform `supabase_url` |
| G | Supabase publishable key | `sb_publishable_…` | no (public) | Step 2.3 | Terraform `supabase_publishable_key` |
| S | Supabase secret key | `sb_secret_…` | **secret** | Step 2.3 | Secret Manager `supabase-secret-key` |
| H | Session pooler URL | `postgresql://postgres.<E>:<D>@aws-0-eu-central-1.pooler.supabase.com:5432/postgres?sslmode=require` | **secret** | Step 2.3 | Secret Manager `worker-database-url`, GitHub secret `SUPABASE_DB_URL` |
| P | API URL | `https://firra-api-<number>.europe-west3.run.app` | no | Step 5 output | GitHub var `API_URL` |
| I | Workload identity provider | `projects/<number>/locations/global/workloadIdentityPools/github/providers/github-actions` | no | Step 5 output | GitHub var `WIF_PROVIDER` |
| J | Deployer service account | `firra-deployer@<A>.iam.gserviceaccount.com` | no | Step 5 output | GitHub var `GCP_DEPLOYER_SA` |

---

## Step 1 — Google Cloud project and Terraform state bucket

### 1.1 Billing account (values B and C)

1. Open https://console.cloud.google.com/billing.
2. If you have no billing account: **Create account** (or **Start free trial**), enter the payment
   method and finish the wizard.
3. In the billing accounts list, copy the **Billing account ID** (format `XXXXXX-XXXXXX-XXXXXX`)
   → value **B**.
4. Note the account currency (shown on the account's **Overview** page, or run the command below
   after 1.2) → value **C**.

```bash
gcloud billing accounts describe <B> --format='value(currencyCode)'
```

### 1.2 Log in with gcloud

Two logins are needed: one for `gcloud` itself and one ("application default credentials") that
Terraform uses. Each opens a browser window; approve with the same Google account.

```bash
gcloud auth login
gcloud auth application-default login
```

### 1.3 Create the project (value A)

A GCP project has three identifiers; only the **Project ID** is used anywhere in this guide:

| Console label | Example | Used for |
|---|---|---|
| Project name | `firra-staging` | display only |
| Project number | `388979587080` | internal; Terraform reads it itself |
| **Project ID** | `firra-509914` | **value A**: every command and `project_id` |

In zsh, `$NAME` inserts a variable. Write either the literal id (`firra-509914`) or `"$PROJECT_ID"`
after `export PROJECT_ID=…`, never `"$firra-509914"` (that expands to `-509914`).

If you already created the project in the console, skip `gcloud projects create` below and just
run the remaining commands with its Project ID.

Pick a globally unique id: lowercase, 6–30 characters, e.g. `firra-staging-` plus 4 random
characters. That is value **A**.

```bash
export PROJECT_ID=<A>
export BILLING_ACCOUNT_ID=<B>

gcloud projects create "$PROJECT_ID" --name="Firra staging"
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT_ID"
gcloud config set project "$PROJECT_ID"
gcloud auth application-default set-quota-project "$PROJECT_ID"

# APIs Terraform needs before it can manage the rest.
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com \
  cloudbilling.googleapis.com billingbudgets.googleapis.com storage.googleapis.com
```

If `projects create` says the id is taken, choose another suffix. If `billing projects link` fails
with a permission error, your Google account needs **Billing Account User** (or Administrator) on
that billing account (Billing → Account management → Add principal).

### 1.4 Terraform state bucket

Terraform keeps its state in a versioned bucket in the same project.

```bash
gcloud storage buckets create "gs://$PROJECT_ID-tfstate" \
  --location=europe-west3 --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://$PROJECT_ID-tfstate" --versioning
```

**Check**

```bash
gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)'   # True
gcloud storage buckets describe "gs://$PROJECT_ID-tfstate" --format='value(location,versioning_enabled)'
# EUROPE-WEST3  True
```

---

## Step 2 — Supabase staging project

### 2.1 Organization

1. Open https://supabase.com/dashboard.
2. If you have no organization: **New organization** → name `Firra`, type **Company**, plan
   **Free** → **Create organization**.

### 2.2 Create the project (value D)

1. In the organization, click **New project**.
2. Fill in:
   - **Project name:** `firra-staging`
   - **Database password:** click **Generate a password**, then copy it into your password manager
     → value **D**. You cannot view it again later (only reset it).
   - **Region:** **Central EU (Frankfurt)**. Do not pick any other region.
   - **Plan / compute:** Free (staging only).
3. Click **Create new project** and wait until the status is healthy (1–3 minutes).

### 2.3 Collect keys and connection string (values E, F, G, S, H)

1. **Project ref (E):** it is the id in the browser URL:
   `https://supabase.com/dashboard/project/<E>`. It is also under **Project Settings → General →
   Project ID**.
2. **Project URL (F):** `https://<E>.supabase.co`. You can confirm it under **Project Settings →
   Data API** (Project URL).
3. **Publishable key (G):** **Project Settings → API Keys**. Copy the key that starts with
   `sb_publishable_`. It is safe to expose (it only grants what RLS allows).
4. **Secret key (S):** on the same page, section **Secret keys** → **+ New secret key** → name
   `firra-api` → create, then reveal and copy the value starting with `sb_secret_` into your
   password manager. It bypasses RLS, so treat it like a root password.
5. **Session pooler URL (H):**
   1. Click **Connect** at the top of the project dashboard.
   2. Tab **Connection string**, type **URI**, method **Session pooler** (not "Direct connection":
      direct connections are IPv6-only and GitHub runners and Cloud Run have no IPv6).
   3. Copy the URI. It looks like
      `postgresql://postgres.<E>:[YOUR-PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:5432/postgres`.
   4. Replace `[YOUR-PASSWORD]` with the **URL-encoded** password D. Encode it without it
      appearing in your shell history:

      ```bash
      python3 -c 'import getpass, urllib.parse; print(urllib.parse.quote(getpass.getpass("DB password: "), safe=""))'
      ```

   5. Append `?sslmode=require`. The final value **H** is:
      `postgresql://postgres.<E>:<encoded D>@aws-0-eu-central-1.pooler.supabase.com:5432/postgres?sslmode=require`
   6. Store H in your password manager.

### 2.4 Email sign-in

1. **Authentication → Sign In / Providers** (left sidebar: Authentication, then Sign In / Providers).
2. **Email** must be enabled, with **Confirm email** on (default). Magic links need nothing else.

### 2.5 Email delivery (choose one)

Supabase's built-in mailer only sends to **members of your Supabase organization** and only a few
emails per hour. Pick one option:

- **Option A — no domain yet (quick):** invite the addresses you will test with (owner and admin)
  to the organization: organization page → **Team** → **Invite** → enter each email, role
  **Developer** → they accept the invitation email. Then those addresses can receive magic links.
- **Option B — own domain with Resend (recommended once you have a domain):**
  1. https://resend.com → sign up → **Domains → Add domain** (choose an EU region if offered) →
     add the shown DNS records at your DNS provider → wait for **Verified**.
  2. **API Keys → Create API key** (permission: Sending access) → copy it (secret).
  3. In Supabase: **Authentication → Emails → SMTP Settings** → enable custom SMTP:
     - Sender email: `no-reply@<your domain>`, Sender name: `Firra`
     - Host `smtp.resend.com`, Port `465`, Username `resend`, Password = the Resend API key
     - Save.
  4. Then raise the email rate limit under **Authentication → Rate Limits** if needed.

### 2.6 Optional: asymmetric JWT signing keys

**Project Settings → JWT Keys**: if offered, migrate to the new (asymmetric) signing keys. Then the
web app and api verify sessions locally instead of calling Supabase Auth. The code works either way,
so you can skip this for P0.

**Check**

```bash
curl -s -o /dev/null -w '%{http_code}\n' "https://<E>.supabase.co/auth/v1/health" -H "apikey: <G>"   # 200
```

---

## Step 3 — Terraform, first pass (everything except the services)

### 3.1 Fill in the variables

```bash
cd infra/terraform/envs/staging
cp terraform.tfvars.example terraform.tfvars      # git-ignored; never commit it
```

Open `terraform.tfvars` and set:

| Field | Value |
|---|---|
| `project_id` | A |
| `billing_account_id` | B |
| `budget_amount` | `20` (monthly amount; alerts at 50/80/100%) |
| `budget_currency` | C (must match the billing account, e.g. `"EUR"`) |
| `alert_email` | the address that should get budget and uptime alerts |
| `supabase_url` | F: the bare project URL, e.g. `https://abcd1234.supabase.co`. Not the `…/rest/v1/` API URL (Terraform rejects it). |
| `supabase_publishable_key` | G |
| `create_services` | `false` (services come in step 5, after the secrets exist) |
| `enable_sentry` | `false` |
| `enable_resend` | `false` (set `true` + `email_from` later if you use Resend for invite emails) |
| `email_from` | `""` |

### 3.2 Apply

```bash
terraform init -backend-config="bucket=$PROJECT_ID-tfstate"
terraform plan        # review: roughly 50 resources to add, 0 to change or destroy
terraform apply       # type "yes"
```

This takes a few minutes (enabling APIs is the slow part).

**If it fails:**

| Error mentions | Fix |
|---|---|
| `SERVICE_DISABLED` / "API has not been used in project" | APIs are still propagating. Wait 1–2 minutes and run `terraform apply` again. |
| `billingbudgets` / permission denied on the budget | Your account needs **Billing Account Costs Manager** or **Administrator** on billing account B. |
| currency mismatch on the budget | Set `budget_currency` to value C. |

**Check**

```bash
gcloud secrets list --format='value(name)'                            # supabase-secret-key, worker-database-url
gcloud tasks queues list --location=europe-west3 --format='value(name)' | wc -l   # 6
gcloud iam service-accounts list --format='value(email)' | grep firra  # 5 service accounts
```

---

## Step 4 — Secret values in Secret Manager

The secrets exist but are empty. Add their values from your password manager. This helper reads
the value without echoing it or saving it to shell history:

```bash
add_secret() {
  local v
  read -rs "v?Value for $1: "; echo
  printf '%s' "$v" | gcloud secrets versions add "$1" --data-file=- --project "$PROJECT_ID"
}

add_secret supabase-secret-key       # paste S (sb_secret_…)
add_secret worker-database-url       # paste H (session pooler URL with ?sslmode=require)
```

Only if you enabled them in `terraform.tfvars`:

```bash
add_secret resend-api-key            # enable_resend = true
add_secret sentry-dsn-api            # enable_sentry = true (EU region DSNs: …ingest.de.sentry.io…)
add_secret sentry-dsn-worker
```

To fix a wrong value, run `add_secret` again: the newest version wins.

**Check**

```bash
gcloud secrets versions list supabase-secret-key --format='value(name,state)'   # 1  ENABLED
gcloud secrets versions list worker-database-url --format='value(name,state)'   # 1  ENABLED
```

---

## Step 5 — Terraform, second pass (Cloud Run services)

1. In `infra/terraform/envs/staging/terraform.tfvars` set `create_services = true`.
2. Apply and read the outputs:

```bash
cd infra/terraform/envs/staging
terraform apply                        # type "yes"
terraform output
```

3. Copy these from the output:
   - `urls.api` → value **P**
   - `workload_identity_provider` → value **I**
   - `deployer_service_account` → value **J**

The services run a Google placeholder image until the first GitHub deploy replaces it. That is
expected.

**Check**

```bash
gcloud run services list --region=europe-west3 --format='value(metadata.name)'   # firra-api, firra-worker
curl -s -o /dev/null -w '%{http_code}\n' "<P>/health"                           # 200 once the api image is deployed
```

---

## Step 6 — Supabase auth URLs (and optional Google / Microsoft login)

### 6.1 Redirect URLs (required)

1. Supabase dashboard → **Authentication → URL Configuration**.
2. **Redirect URLs → Add URL:** `firra://auth-callback` (the Mac app's sign-in return address) → **Save URLs**. There is no web app: the Site URL can stay at its default, or point to the API.

Without the redirect URL, magic links and OAuth cannot return to the Mac app.

### 6.2 Google sign-in (optional for P0)

1. Google Cloud console, project A → **APIs & Services → OAuth consent screen** (Google Auth
   Platform):
   - App name `Firra (staging)`, support email = yours, audience **External**, publishing status
     **Testing**.
   - **Test users → Add users:** the Google addresses you will sign in with.
2. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type **Web application**, name `supabase-staging`.
   - **Authorized redirect URIs:** `https://<E>.supabase.co/auth/v1/callback`.
   - Create, then copy the **Client ID** and **Client secret**.
3. Supabase → **Authentication → Sign In / Providers → Google** → enable → paste Client ID and
   Client secret → **Save**.

### 6.3 Microsoft sign-in (optional for P0)

1. https://entra.microsoft.com → **Applications → App registrations → New registration**:
   - Name `Firra staging`.
   - Supported account types: **Accounts in any organizational directory and personal Microsoft
     accounts**.
   - Redirect URI: platform **Web**, `https://<E>.supabase.co/auth/v1/callback`.
   - **Register**, then copy **Application (client) ID** from the overview page.
2. **Certificates & secrets → Client secrets → New client secret** (expiry 6–12 months) → copy the
   **Value** column immediately (it is shown only once).
3. Supabase → **Authentication → Sign In / Providers → Azure** → enable → Client ID, Secret, and
   Azure Tenant URL `https://login.microsoftonline.com/common/v2.0` → **Save**.

---

Mailbox connections (Gmail, Google Calendar, Outlook) use separate OAuth apps; set them up with
`docs/runbooks/mailbox-connections.md` once staging runs.

---

## Step 7 — GitHub environment, variables and secret

All of this is on https://github.com/malikibric/fira/settings (you need admin rights).

### 7.1 Environment `staging`

1. **Settings → Environments → New environment** → name `staging` (exactly) → **Configure
   environment**.
2. **Deployment branches and tags** → choose **Selected branches and tags** → **Add deployment
   branch or tag rule** → pattern `main` → **Add rule**.
   Only jobs from `main` can then deploy, and the GCP trust (step 3) only accepts this environment.

### 7.2 Environment variables

Same page, section **Environment variables → Add environment variable**. Add each:

| Name | Value |
|---|---|
| `GCP_PROJECT_ID` | A |
| `WIF_PROVIDER` | I |
| `GCP_DEPLOYER_SA` | J |
| `API_URL` | P (no trailing slash) |

(`SMOKE_WORKSPACE_ID` is added later, in "Acceptance" below.)

### 7.3 Environment secret

Section **Environment secrets → Add environment secret**:

| Name | Value |
|---|---|
| `SUPABASE_DB_URL` | H (session pooler URL, used to run migrations) |

### 7.4 Same thing from the terminal (alternative to 7.1–7.3)

```bash
REPO=malikibric/fira
gh api -X PUT "repos/$REPO/environments/staging" --input - <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
gh api -X POST "repos/$REPO/environments/staging/deployment-branch-policies" -f name=main -f type=branch

cd infra/terraform/envs/staging
gh variable set GCP_PROJECT_ID  --env staging --repo $REPO --body "$PROJECT_ID"
gh variable set WIF_PROVIDER    --env staging --repo $REPO --body "$(terraform output -raw workload_identity_provider)"
gh variable set GCP_DEPLOYER_SA --env staging --repo $REPO --body "$(terraform output -raw deployer_service_account)"
gh variable set API_URL         --env staging --repo $REPO --body "$(terraform output -json urls | jq -r .api)"
gh secret set SUPABASE_DB_URL   --env staging --repo $REPO     # prompts; paste H
```

### 7.5 Repository security settings (recommended)

- **Settings → Advanced Security** (or **Code security**): enable **Dependabot alerts**,
  **Dependabot security updates** and **Secret scanning / Push protection**.
- **Settings → Rules → Rulesets → New branch ruleset** for `main`, after the first CI run (so the
  check names exist): require a pull request and the status checks from the **CI** workflow.

**Check**

```bash
gh variable list --env staging --repo malikibric/fira   # 5 variables
gh secret list   --env staging --repo malikibric/fira   # SUPABASE_DB_URL
```

---

## After the seven steps: first deploy

1. Push `main`:

   ```bash
   git push -u origin main
   ```

2. Watch **Actions** on GitHub (or `gh run watch`): **CI** runs first; when it is green,
   **Deploy staging** starts automatically. It:
   1. logs in to GCP via Workload Identity Federation (no keys),
   2. builds and pushes the worker and api images to Artifact Registry,
   3. runs the database migrations (`supabase db push`),
   4. deploys worker → api,
   5. checks `/health` and `/ready`.
3. Open `<P>/health`: it answers `ok`.

**If the push or Deploy staging fails:**

| Step / error | Likely cause | Fix |
|---|---|---|
| `git push`: `push declined due to email privacy restrictions` | Commits carry your private email and GitHub blocks that | https://github.com/settings/emails → untick **Block command line pushes that expose my email**, or set `git config user.email <id>+<user>@users.noreply.github.com` before committing |
| Health checks: `/health` 404 | Old image serving `/healthz` (Cloud Run reserves paths ending in `z`) | Deploy the current `main` |
| Health checks: api `/ready` 503 | api cannot reach Supabase: `supabase_url` has a path (e.g. `/rest/v1/`) or the secret key is wrong | Fix `terraform.tfvars` (bare URL) and `terraform apply`; or re-add `supabase-secret-key` (step 4) |
| Auth: `Permission 'iam.serviceAccounts.getAccessToken' denied` / `unauthorized_client` | Environment not named exactly `staging`, or job not from `main` | Check 7.1; re-run from `main` |
| Push: `denied: Permission "artifactregistry.repositories.uploadArtifacts"` | Step 5 not applied | Run step 5 again |
| Apply database migrations: connection error | H wrong (direct instead of session pooler, password not URL-encoded) | Re-create H (2.3), update GitHub secret and `worker-database-url` |
| Deploy: revision fails to start | A secret has no version | Step 4, then re-run the workflow |
| `/ready` returns 503 | api cannot reach Supabase (URL or secret key wrong) | Check F in tfvars and value S |

## Acceptance check in staging (P0)

1. **Sign-up and workspace:** open the Mac app, sign in with the owner address (magic link or
   Google/Microsoft) and create a workspace.
2. **Invite an admin:** **Team** → enter the admin address → **Invite** → send the code. On the other
   Mac (or another macOS user) the admin signs in, chooses **Join** and enters the code. Both people now
   appear under Team.
3. **Task round-trip through Cloud Tasks:**
   1. Copy the workspace id (`GET /v1/me` with the owner's access token lists it under `memberships`).
   2. Add it as a variable and re-run the deploy:

      ```bash
      gh variable set SMOKE_WORKSPACE_ID --env staging --repo malikibric/fira --body "<workspace id>"
      gh run list --workflow "Deploy staging" --limit 1      # note the run id
      gh run rerun <run id>
      ```

   3. The step **Task round-trip (api -> Cloud Tasks -> worker -> DB)** must finish with
      `succeeded`.
4. **Optional, RLS tests against staging** (every test rolls back, nothing is left behind):

   ```bash
   read -rs "DBURL?Session pooler URL (H): "; echo
   pnpm exec supabase test db --db-url "$DBURL"; unset DBURL
   ```

If the round-trip stays `queued` or the worker rejects deliveries (403/404 in the `process` queue):
Cloud Tasks may not count as internal traffic for the worker. In
`infra/terraform/envs/staging/main.tf` add `worker_ingress = "INGRESS_TRAFFIC_ALL"` to the module
block and run `terraform apply`. The worker still accepts only requests carrying the Cloud Tasks
OIDC token, so this does not open it up.

When all three checks pass, P0 is accepted in staging.

## Production (later, before the first customer)

Same steps with `infra/terraform/envs/prod`, a separate GCP project (`firra-prod-…`), a separate
Supabase project on a paid plan, and a GitHub environment named `production` with **Required
reviewers** enabled. Deploy by pushing a tag on `main`: `git tag v0.1.0 && git push origin v0.1.0`.
