# Synthetic dataset "Northbeam Growth"

Fictional US B2B marketing agency with 15 clients (12 active, 3 churned), 12 months, **700
messages** and about 300 calendar events (P2 D17; spec `northbeam.v3`, P2b D12). Every company, person and domain is invented
(`.example` domains). Used for evals, demos and local development — never as customer data.

| File | What | Made by | Cost |
|---|---|---|---|
| `services/worker/synthetic/northbeam/spec.yaml` | people, clients, routine topics, planted scenarios with expected outcomes | hand-written | — |
| `services/worker/synthetic/northbeam/truth.json` | ground truth for the evals (expected items, signals, latent needs, forbidden quotes, and per client the account state to reach and by when) | `python -m firra.synthetic truth` | free |
| `services/worker/synthetic/northbeam/corpus/` | `messages.jsonl`, `events.jsonl`, `manifest.json` | `python -m firra.synthetic generate --yes` | **Vertex, once** |

Commands run from `services/worker` with the repository's virtualenv.

## What v3 adds (P2b)

- **Account expectations:** every active client states `assessment: {risk, expansion, play, by}`
  (the state at the as-of date, and the earliest weekly as-of by which the risk state must be
  reached), optional `max_risk` (never above it in the last 180 days) and `caveats`; churned clients
  state `detect_by` (elevated or high at least 30 days before `churned_at`).
- **B accounts:** `umber` (the decision maker goes quiet, fewer people write, one scope-cut remark —
  no complaint, no negative tone) and `vantage` (new VP, UK launch, bigger budget, an adjacent ask
  twice below the latent-need threshold).
- **Benign controls:** `pinecrest` has a busy spring and a quiet August (`max_risk: watch`);
  `halden` loses Gus Meyer's mailbox on 2026-09-05. The loader creates **one mailbox connection
  per firm person**; a message lands in the first firm participant's mailbox that is still
  connected, mail only a revoked mailbox would have seen is not ingested, and that connection ends
  `revoked` with `last_synced_at` = the revocation date. Signals that fire on the raw data but must
  be hidden by the coverage guard are listed as `suppressed_signals`.
- **Planner knobs:** `volume_windows` (routine-traffic weight between two dates; factors multiply
  with `volume_after`) and `writers_after` (from a date, only these client people write).
- **Budget talk** items state `budget_direction`; `initiative` items state `initiative_type`
  (extraction v2).

`tests/unit/test_synthetic_signals.py` builds signal facts straight from the plan, as if ingest and
extraction were perfect (`firra/synthetic/facts.py`), and asserts that the implemented rules fire
exactly the expected signals for every client — free, before any paid run, so a failing paid eval
points at extraction, not at the dataset.

## Free commands

```bash
python -m firra.synthetic plan    # counts per client and the generation estimate (no calls)
python -m firra.synthetic truth   # rewrite truth.json after editing spec.yaml (CI checks drift)
```

## Generate the corpus (costs money — owner approval first)

Not run yet (owner decision 2026-09-29: no spend for now). `plan` prints the estimate (about
0.8 USD with `gemini-3.8-flash`). The run stops at `FIRRA_EVAL_BUDGET_USD` (default 5) and does
nothing without `--yes`.

```bash
export FIRRA_GCP_PROJECT_ID=firra-509914 FIRRA_VERTEX_LOCATION=eu \
       FIRRA_MODEL_SYNTHDATA=gemini-3.8-flash FIRRA_EVAL_BUDGET_USD=2
export FIRRA_VERTEX_ACCESS_TOKEN="$(gcloud auth print-access-token)"   # valid for 1 hour
python -m firra.synthetic generate          # prints the estimate, spends nothing
python -m firra.synthetic generate --yes    # writes synthetic/northbeam/corpus/
```

Your user needs `roles/aiplatform.user` on the project. Review a sample of the bodies, then
commit the corpus; regenerating later changes every eval baseline, so do it only when the spec
changes.

## Load into a workspace

```bash
eval "$(scripts/local-db.sh start)"   # bundled Postgres; prints FIRRA_DATABASE_URL
export FIRRA_LOCAL_RAW_DIR=/tmp/firra-raw
python -m firra.synthetic load --create "Northbeam demo"      # or --workspace-id <uuid>
```

Loading goes through the real ingest path (minimization, `metadata_only` for Meridian Payroll,
cleaning, `pending` marking) and refuses a workspace that already has clients. Processing then
runs as usual through the owner's AI executors (or the in-conversation pair); the legacy paid path
calls Gemini only where the pre-F9 Vertex code still exists.
