# Vertex AI (Gemini) — staging setup

P2 decisions D1, D16, D18 in the design notes. Gemini runs only on the Vertex AI **EU
multi-region** endpoint (`eu`, `https://aiplatform.eu.rep.googleapis.com`): data at rest and ML
processing stay in EU member states. Models: `gemini-3.5-flash-lite` (extraction),
`gemini-3.8-flash` (synthesis), `gemini-embedding-001` (embeddings) — all confirmed by the
`diagnostics.llm_probe` task (step 3) before a feature relies on them.

## 1. Billing

The staging project must have an active billing account (it already has one for Cloud Run).
Vertex AI is billed per token; evals cost about 1–2 USD per run (weekly, 700-message corpus), the probe a fraction of
a cent. The staging budget alert (`budget_amount` in `terraform.tfvars`) warns by email.

## 2. Terraform (before merging code that calls Vertex)

Adds: the `aiplatform.googleapis.com` API, `roles/aiplatform.user` for the worker, the
`firra-evals` service account (Vertex only, impersonated by GitHub Actions in the `staging`
environment), and the worker env `FIRRA_VERTEX_LOCATION`, `FIRRA_MODEL_EXTRACT`,
`FIRRA_MODEL_SYNTH`, `FIRRA_MODEL_EMBED` (the current worker ignores them until P2 code ships).

```bash
cd infra/terraform/envs/staging
git fetch origin && git checkout p2        # or the branch of the P2 PR
terraform init
terraform plan -out p2.tfplan              # expect: 1 API, 1 SA, 3 IAM bindings, worker env change
terraform apply p2.tfplan

REPO=malikibric/fira
gh variable set GCP_EVALS_SA --env staging --repo $REPO --body "$(terraform output -raw evals_service_account)"
```

**Check**

```bash
gcloud services list --enabled --project "$PROJECT_ID" | grep aiplatform
gcloud run services describe firra-worker --region=europe-west3 --project "$PROJECT_ID" \
  --format='value(spec.template.spec.containers[0].env)' | tr ';' '\n' | grep -E 'VERTEX|MODEL'
gh variable list --env staging --repo malikibric/fira | grep GCP_EVALS_SA
```

## 3. Probe (runs after every staging deploy)

`diagnostics.llm_probe` calls each configured model once on the `eu` endpoint with a fixed,
fictional prompt (a fraction of a cent) and records tokens and cost in `llm_usage` of the smoke
workspace. The deploy workflow runs it after the task round-trip (step **LLM probe**, needs
`SMOKE_WORKSPACE_ID`); it prints per model `ok`, `error_class`, `provider_status` and duration and
warns without failing the deploy. A model that is not served in `eu` shows `provider_status: 404`
there — switch `llm_models` in Terraform to one that is, apply, re-run the workflow.

Manual re-run: **Actions → Deploy staging → Re-run all jobs**, or

```bash
TOKEN=$(gcloud auth print-identity-token --impersonate-service-account="$(terraform output -raw deployer_service_account)" \
  --audiences="$API_URL" --include-email)
curl -X POST "$API_URL/internal/v1/diagnostics/llm-probe" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d "{\"workspace_id\":\"$SMOKE_WORKSPACE_ID\"}"
```

(The manual call needs `roles/iam.serviceAccountTokenCreator` on the deployer for your user.)

## 4. Spend switches (both off by default)

Nothing calls Gemini in an environment until the owner switches it on:

- **Extraction** (`process.records`, one paid call per new client email): Terraform variable
  `processing_enabled = true` in `infra/terraform/envs/<env>/main.tf` (module input), then
  `terraform apply`. It sets `FIRRA_PROCESSING_ENABLED=true` on the worker. While off, records stay
  `pending`; the body purge (72 hours, at most 7 days) still removes their bodies and counts them as `skipped_purged`.
- **Deploy probe** (section 3): GitHub environment variable `LLM_PROBE=on`
  (`gh variable set LLM_PROBE --env staging --repo malikibric/fira --body on`).

## Embedding location

Google lists the generation models (`gemini-3.5-flash-lite`, `gemini-3.8-flash`) for the `eu`
multi-region but `gemini-embedding-001` only for single regions (documented 2026-10-01; confirm in
Model Garden). Set `FIRRA_VERTEX_EMBED_LOCATION=europe-west3` to send embeddings there while
generation stays on `eu`; both stay in the EU. The eval workflow already sets it. Staging and prod
Cloud Run still use one location for both until `FIRRA_VERTEX_EMBED_LOCATION` is added to the
Terraform worker environment (infra change, owner approval); run `diagnostics.llm_probe` once (it
costs a fraction of a cent) before processing is enabled there.

## Schema limits are not sent

`responseJsonSchema` is rejected with a bare `400 INVALID_ARGUMENT` when it contains `maxLength` or
`maxItems` (found 2026-10-01 by bisecting the extraction schema; the error has no detail, and a 400
is not billed). `VertexClient` therefore removes those two keywords before sending; the Pydantic
models still enforce the limits on the answer. If a new schema gets a bare 400, bisect it the same
way (drop keywords one at a time against the model, 400s are free).
