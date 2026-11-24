# Score a processed Northbeam workspace

> **Local v1:** scoring and replay below run against the **bundled Postgres** — start it with
> `eval "$(scripts/local-db.sh start)"` and use the printed `FIRRA_DATABASE_URL` wherever this file
> still shows the old local-Supabase URL (`127.0.0.1:54322`). The paid Vertex/GPU sections ("Run the
> pipeline", "GitHub workflow", "Prompt lab", T15) describe the legacy cloud-era flow: current evals
> (incl. the L3-06a executor eval) run on **recorded outputs and fakes only**; a real executor run needs
> the owner's explicit go on their own subscription. The legacy Vertex code disappears in phase F9.

T02 scores an already processed local Northbeam workspace. It only reads Postgres, the
committed `services/worker/synthetic/northbeam/truth.json`, and a local corpus; it does not run the
pipeline or call a model. T03 adds the guarded `run` command and an off-by-default scheduled workflow.

From `services/worker`, with local Supabase running and the synthetic workspace already loaded and
processed:

```bash
export FIRRA_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
uv run python -m firra.evals score --workspace <workspace-uuid> --out /tmp/northbeam-eval
```

The command writes `eval.json` and `eval.md`. Exit code 0 means every threshold passes; 1 means
at least one fails; 2 means the database URL or corpus file is missing. The URL must point to
local Postgres. `--corpus-dir` can select another corpus directory; it defaults to
`services/worker/synthetic/northbeam/corpus`. The corpus has not been generated yet. Citation
quotes are checked against its message bodies, independently of the processed database text.
Signals, latent needs and cards are scored from supplied observations in T02 tests; their database
collection starts when T01's tables land.

| Metric | Meaning | Threshold |
|---|---|---|
| Citation validity | Evidence quotes found verbatim in cleaned source bodies | 1.0 |
| Extraction recall / precision | One-to-one matches by source ID, kind, optional subtype and normalized quote | Reported |
| Injection items | Observed items carrying a forbidden prompt-injection quote | 0 |
| Signal recall / precision | Matches by client and signal type, also reported per type | Recall ≥ 0.8 |
| Unverified tone/escalation/complaint signals | Such signals without a verified evidence ID | 0 |
| Latent needs | Planted needs with exact mention and thread counts; prohibited topics | All exact; 0 prohibited |
| False-positive cards | Cards with no expected source signal for their client, per 10 active clients | ≤ 1 |
| Verify drop rate | Quote verification drops divided by attempted extraction items | Reported |
| Cost | Input/output tokens and USD by model and overall | Reported |

Failures list identifiers, never quote bodies. No observed evidence produces citation validity 0,
so an empty run cannot pass.

## Replay a local workspace (Churn Replay, free)

No model calls: it reuses the extractions already in the database.

```bash
export FIRRA_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
uv run python -m firra.assessment replay --workspace <uuid> --from 2026-04-01 --to 2026-09-30 --step 7
```

It creates a `replay_run`, writes weekly `account_assessment` rows (`run_kind = replay`) and prints
the summary: per client the detection date, lead days before the churn notice (or churn date),
max state, elevated weeks, flicker and a miss reason; totals for losses detected ≥ 30 days ahead,
median lead and elevated weeks on retained clients. The URL must point to local Postgres. In
staging a replay runs as task `assessment.replay` after an owner/admin calls `request_replay_run`.

### Churn Replay report

```bash
uv run python -m firra.assessment report --workspace <uuid> --run <replay-run-uuid> --out /tmp/replay
```

Writes `index.md` (lost clients with detection date and lead days, retained clients with elevated
weeks) and one Markdown report per client: what was visible when, the state changes, the messages
behind each driver (verbatim, at most 25 words, with deep links) and the recommended play. Local
Postgres only; the run must have finished (`succeeded`).

## Run the pipeline (paid, owner authorization required)

Every run creates a fresh local workspace, loads the corpus through ingest, processes it, then
rebuilds memory with the committed truth's `as_of` date and scores. Only synthetic data is used.
The committed Northbeam v3 corpus is available from #57. Preparing this implementation and its tests does not authorize paid calls.

With local Supabase running, authenticate your account (it needs Vertex permissions on the staging
project), and start a dedicated local worker. From the repository root:

```bash
gcloud auth login
export FIRRA_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
export FIRRA_ENV=local FIRRA_TASK_AUTH_MODE=local_secret FIRRA_KMS_MODE=local
export FIRRA_LOCAL_TASK_SECRET=$(openssl rand -hex 32)
export FIRRA_LOCAL_KEK_HEX=$(openssl rand -hex 32)
export FIRRA_LOCAL_RAW_DIR=/tmp/firra-eval-raw
export FIRRA_LOCAL_WORKER_URL=http://127.0.0.1:8080
export FIRRA_VERTEX_TOKEN_SOURCE=gcloud FIRRA_VERTEX_LOCATION=eu
export FIRRA_GCP_PROJECT_ID=<staging-project-id>
export FIRRA_MODEL_EXTRACT=gemini-3.5-flash-lite FIRRA_MODEL_SYNTH=gemini-3.8-flash
export FIRRA_MODEL_EMBED=gemini-embedding-001
export FIRRA_EVAL_BUDGET_USD=1.75 FIRRA_LLM_HARD_CAP_USD=1.75
export FIRRA_PROCESSING_ENABLED=true
uv run uvicorn --factory firra.app:create_app --host 127.0.0.1 --port 8080
```

Run the following commands in another terminal with the same database URL, local task secret and
budget variables. The first prints the corpus size and the D18 estimate (about 1–2 USD) without
loading anything or requesting a model call:

```bash
uv run python -m firra.evals run --out eval-out
uv run python -m firra.evals run --yes --out eval-out
```

`--budget-usd` overrides `FIRRA_EVAL_BUDGET_USD` (default 1.75). Start the worker with
`FIRRA_LLM_HARD_CAP_USD` equal to that chosen budget before confirming the run. The runner sets the
new workspace's monthly budget too, but new workspaces normally receive a 150 USD first-month
backfill allowance: the worker hard cap limits that allowance. It always takes the smaller of the
normal limit and the cap, and all 50/80/100 threshold events use that effective limit. Unset leaves
normal production behavior unchanged; any environment may set a positive cap to reduce spend.
The cap is checked before each model call, so one batch in flight can overshoot slightly; the billed call that crosses the limit is recorded and the
next call stops. It is not a provider-side reservation or an exact upper bound on one call's cost.

`FIRRA_VERTEX_TOKEN_SOURCE` defaults to `metadata`. `gcloud` runs only for Vertex in local/test,
caches access tokens for 45 minutes and never prints command output or tokens. KMS/GCS/queue
clients keep metadata tokens; evals use local encryption, local raw storage and the local queue.

`--worker-url` defaults to `http://127.0.0.1:8080`; `--timeout-min` defaults to 100 for the entire
pipeline run; `--corpus-dir` overrides the committed corpus path. The worker must already be
healthy. Settlement means no queued/running job and no pending record on two polls ten seconds
apart. The workspace is retained for local inspection; use its id from `run.json` with `score`.
Artifacts are `run.json` (ids, job states/errors, pending count and usage totals only), plus
`eval.json` and `eval.md` only if the pipeline completes. Scoring uses the same collector and
thresholds as `score`.

After the pinned memory rebuild, `run` requests `synth.cards` for the Monday of the `as_of` week (needs the synth model, same budget cap) and waits for it. It then creates a replay run over the 12 months ending at `truth.as_of` (weekly steps; two of the three losses churned more than 180 days earlier, and a churned client's replay stops at its churn date)
and runs it in the same process: deterministic, no queue and no model calls. Its weekly assessments
are scored against the dated expectations in `truth.json` and added to `eval.json` (`replay`) and
`eval.md`, so the exit code covers both. Metrics and thresholds: risk, expansion and play exact at
`as_of` (each ≥ 0.8); `by` dates met (risk and expansion reached by the date); churned clients
detected by `detect_by` (≥ 2 of 3); benign controls never above `max_risk`; false-alarm client-weeks
per 10 clients per month ≤ 1 (weeks at `elevated` or above on clients truth expects at
`stable`/`watch`); flicker 0. Cards: a card on a client truth expects to only `monitor`, or on a client truth does not know (kept under `unknown_client`), is a false positive; its justification is its primary drivers, not every driver that fired. Live assessments dated after `as_of` (the unpinned follow-up runs on today's date) are discarded before the cards stage so cards word the evaluated state. Needs match one-to-one with the closest counts. A churned client without `detect_by` fails the run instead of dropping out of the 2-of-3 gate. Signal recall counts a signal when the replay raised it in any week of the account's life (union of the weekly drivers, with their evidence); the live `signal` table holds only what is active at `as_of`, so planted warnings from months earlier and all signals of lost clients would be invisible to it. Latent needs are read from `latent_need` (service mapped to its spec key). Stored cards are re-checked against their own stored facts and verified quotes (`check_card`): any number, number word or long quotation not in the facts is a numeric-integrity failure (0 allowed), and a run whose truth expects plays but wrote no card fails (a card stage that wrote nothing is not a pass). Extraction items must also carry the right `budget_direction` / `initiative_type` when the truth states them. Lead days (before notice or, without one, churn) and their median are reported for the losses. Missing expected caveats are listed but do not fail the run. The card
numeric-integrity check joins once cards (T23b) are merged. Failures list client keys only.

| Exit | Meaning |
|---|---|
| 0 | All thresholds pass, or an unconfirmed preview completed without writes |
| 1 | Pipeline completed; at least one scoring threshold failed |
| 2 | Configuration/guard error before any writes (local DB, corpus, worker health, budget, secret) |
| 3 | Pipeline failure, failed job, pending work at timeout, or timeout; `run.json`, no scoring |

## GitHub workflow (off by default)

Manual: Actions → **Northbeam evals** → Run workflow → `confirm: spend`, or:

```bash
gh workflow run evals.yml -f confirm=spend -f budget_usd=1.75
```

The owner's total budget until milestone A acceptance is **5 USD**: corpus generation uses
`FIRRA_EVAL_BUDGET_USD=1.5`. The only planned eval is one **extract.v3** run capped at
1.75 USD, on `claude/t18-extract-v2` (or after T18 merges). No extract.v1 baseline or v1-v2
comparison is run. Worst-case planned spend is 3.25 USD; the saved 1.75 USD stays unspent.
These per-run caps do not track the shared total; every paid run still requires the owner's go.
The runner uses the checked-out worker's extraction prompt; it does not select or compare versions.

Anything other than `spend` skips the job. Manual `budget_usd` defaults to `1.75`;
scheduled runs use staging `EVALS_BUDGET_USD` or `1.75` when unset. The first step rejects
non-positive, non-finite or above-2 USD budgets with exit 2 before setup or GCP authentication. The workflow uses staging WIF (`WIF_PROVIDER`,
`GCP_EVALS_SA`, `GCP_PROJECT_ID`), a local Supabase and worker, a 1.75 USD default worker cap matching the
run budget, and uploads `eval-out` for 30 days. A missing committed corpus fails before any GCP
authentication step. Worker output is restricted to error-level structured logs. Nothing here sets
repository/environment variables or dispatches a workflow.

Weekly runs are Saturday 03:00 UTC and remain skipped until the owner enables the staging variable:

```bash
gh variable set EVALS_SCHEDULE --env staging --body on
# Disable again:
gh variable set EVALS_SCHEDULE --env staging --body off
```

Expected cost is about 1–2 USD per corpus run (D18); the default cap is 1.75 USD. No Terraform/env
application is required to merge: these switches are optional and off by default. The worker's
first-month allowance is capped only when the hard-cap environment variable is set.

## Before the paid run (T15 readiness)

The workflow checks, before any GCP step, that the checkout uses a current extraction prompt (v2 or later) and that the
corpus files agree with `truth.json`; it runs the worker with a 60 s task budget (so a stuck
provider call cannot outlast the 570 s local delivery timeout) and sends embeddings to
`europe-west3` (`FIRRA_VERTEX_EMBED_LOCATION`) while generation stays on `eu`. Ledger prices are
Google's non-global rates (10% above the global ones), so the hard cap errs early. The ledger is a
precheck without reservation: the last call can cross the cap, and a read timeout on a paid call can
be retried by the HTTP layer without a second ledger entry; treat 1.75 USD as the intended budget,
not an exact provider-side ceiling. Dispatch only from a ref that contains the workflow, the stage
code and extract.v3 (merge first), once, with the owner's go.

## Prompt lab (extraction only, a few cents)

The first real run showed that prompt quality, not the pipeline, decides the eval (the real model
extracts many incidental items from routine mail, which fills latent needs and expansion states).
A full run is too slow and costly to iterate on, so the lab sends a sample through the same prompt,
schema, cleaning and verification: every eligible message that carries planted truth items (42)
plus a seeded sample of routine mail. No database, no worker, no Actions minutes. It prints recall
per kind on the planted items and extra items per kind on routine mail (per 100 messages), the
missing planted items, and a sample of the model's extra quotes (synthetic corpus only).

```bash
cd services/worker
# Free preview: counts and an estimate, nothing is sent.
uv run python -m firra.evals prompt-lab --prompt extract.v3 --routine 60 --seed 1
# Paid (about 0.11 USD for 42 + 60 messages; hard cap with --budget-usd, default 0.30):
FIRRA_GCP_PROJECT_ID=firra-509914 FIRRA_VERTEX_LOCATION=eu \
FIRRA_MODEL_EXTRACT=gemini-3.5-flash-lite \
FIRRA_VERTEX_ACCESS_TOKEN="$(gcloud auth print-access-token)" \
  uv run python -m firra.evals prompt-lab --prompt extract.v3 --routine 60 --seed 1 --yes
```

Compare prompts on the same seed (`--prompt extract.v2` is the earlier baseline). Results are also written
to `lab-out/lab-<prompt>.{txt,json}`. `PROMPT_VERSION` in `firra/extract/process.py` stays on the
production prompt until a candidate wins; then switch it, update the workflow's pre-auth check and
run the full eval once.

Notes on the lab (review fixes, 2026-10-01):

- It refuses any corpus that is not the fictional Northbeam one (message ids on
  `@synthetic.northbeam.example`, every address on a reserved `.example` domain). That is a tripwire
  against pointing it at real mail, not proof of provenance.
- A message that cannot be answered is counted (`errors`) and the run goes on; the result is then
  marked `INCOMPLETE`, noise per 100 routine messages is computed on the completed routine messages
  only (`n/a` when there are none), and the command exits with 1.
- Results are written to `lab-out/lab-<prompt>-<model>-seed<seed>-think<budget>-<digest>.{json,txt}`
  (the digest covers the sampled message ids and the prompt text), so comparisons never overwrite
  each other. `lab-out/` is git-ignored.
- The `need` item kind (extract.v4) shows in the raw kinds line: a statement of need is stored as a
  `request` with `form: need` and scored as the planted `request` it stands for.

## Stalled stages (run 4, 2026-10-01)

A task that outlasts the 570-second local delivery window leaves its job `queued` with nobody to
deliver it (the dispatcher never resends after a timeout). Provider calls are therefore bounded to
about 4 minutes (90 s per attempt, 150 s of retrying) and the cards task stops between cards when its
time budget is spent and continues. As a net, the eval runner delivers again any queued job that
nobody has worked on for 3 minutes while no job is running. The cards stage has its own 20 minute
limit; if it fails or times out the run is still scored and `run.json` shows
`cards_stage: failed` or `timed_out` (the card gate fails the verdict when plays are expected and no
card exists).

Planted latent needs count as found when their mention and thread counts are within 1 of the plan (`NEED_COUNT_TOLERANCE`); a missing need, a wrong service and any prohibited need still fail. Counts are printed as `observed expected planned` in `incorrect_needs` and `needs_within_tolerance` (client, service key and numbers only).
