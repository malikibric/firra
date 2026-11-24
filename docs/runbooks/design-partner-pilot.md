# Design-partner pilot: Churn Replay sessions on real mail (T27)

> **Local v1 (2026-10-05) supersedes this flow:** replay runs in the local engine on the owner's Mac
> (`python -m firra.assessment replay` / task `assessment.replay`), extraction is the owner's own AI
> on their subscription (not Vertex per-mailbox pricing), and deletion is in-app. Kept until rewritten
> for the local pilot; do not follow the staging-workspace, Vertex-cost or GCS steps below.

Status: draft for the owner. Nothing here has been run on real data. Roadmap B founder test:
at least two sessions in which the founder marks a finding "did not know" or "would have noticed
too late" and every finding marked "wrong" is explained.

Product rule 1 allows churned clients for calibration and backtests. A replay is a backtest, so
lost clients are the point of the session; current clients are shown afterwards as the false-alarm
check.

## 1. Before anything touches real mail (owner)

| Gate | Who | Done when |
|---|---|---|
| Written consent to replay the firm's lost clients | partner founder | signed email or document naming the clients by key (A, B, C), not by name, in our copy |
| DPA signed | owner + partner | counsel-reviewed text; covers: Firra as processor, sub-processors (Google Cloud EU: Vertex AI, Cloud Run, KMS, GCS; Supabase EU), EU-only processing, raw bodies deleted once processed and delivered to the user's Mac (72 h undelivered, 7 days at most), hard deletion on request, no audio/video/face analysis, no employee scoring, no customer data used to train models |
| Contributors informed | partner founder | each mailbox owner has seen the "what is collected" notice (invite link) |
| Extraction cost approved | owner | a written go with the estimate from section 3; never implied |

Counsel owns the DPA wording. the legal drafts (not included) is the engineering draft (facts, annexes, a status table of what is built vs planned) for counsel to start from; this runbook only lists what the product does so the text can match.

## 2. Google access (test users)

The Gmail body scope is restricted, so until Google verification and CASA the app runs in
**Testing** with the partner's mailbox owners added as test users. Verify against current Google
documentation before the first session (docs/connectors.md rule), in particular:

- the test-user cap and the "unverified app" warning each user will see;
- refresh tokens issued to an app in Testing expire after 7 days, so connect or reconnect no more
  than a day or two before each session and plan for a revoked-token error ("token revoked,
  reconnect") if a session slips;
- Workspace admins may block unverified apps; ask the partner's admin early.

Setup follows `docs/runbooks/mailbox-connections.md`. Use a separate **staging** workspace per
partner, never a shared one, so deleting it deletes everything.

## 3. Cost estimate per mailbox (before running)

Extraction is the only paid step. From the T15 plan: about 0.0013 USD per full email on
`gemini-3.5-flash-lite` (1.8k input, 300 output tokens), embeddings under 0.01 USD per mailbox.

```
estimate_usd = client_matched_emails_in_12_months x 0.0013 x 1.3 (headroom)
```

Get the count without reading any content: connect, run the backfill with extraction off
(`FIRRA_PROCESSING_ENABLED=false`), then count `source_record` rows of kind `email` with policy
`full` (a count query by `workspace_id`, no bodies). Send the owner the number and the estimate;
set the hard cap (`FIRRA_LLM_HARD_CAP_USD`, added with the eval run in #54) to the approved amount before enabling extraction. A run that reaches
the cap stops with `budget_exceeded`; do not raise it without a new go.

Local operator command (loopback Postgres only), after the extraction-off backfill:

```bash
uv run python -m firra.assessment estimate --workspace <uuid> --months 12
```

It prints only full-policy email counts, client id prefixes/statuses and the estimate; no content.

Example: 4,000 client-matched emails is about 6.8 USD with headroom. The corpus and eval budget
(5 USD) does not cover partner runs; each partner run is its own approval.

## 4. Preparation (day before)

1. Create the partner workspace; add clients (A, B, C for the lost ones, then current) with their
   domains. Set policy `metadata_only` for any client the partner does not want read; `excluded`
   for clients they want left out entirely.
2. Connect mailboxes and calendars; run backfill, then extraction (within the approved cap).
3. Create the replay run over 12 months of history and write the report:

   ```bash
   uv run python -m firra.assessment report --workspace <uuid> --run <run-uuid> --out ~/pilot/<partner>/
   ```

4. Check, without reading customer text: replay summary totals (losses, detected 30 days ahead,
   false alarms per 10 clients per month, flicker) and any `coverage_changed` or `low_data`
   miss reasons. If a lost client has no usable data, say so in the session instead of guessing.

   ```bash
   uv run python -m firra.assessment pilot-check --workspace <uuid> --run <run-uuid>
   ```

   This local-only command prints summary totals and per-client id prefixes/statuses, miss reasons
   and weekly caveat counts. Unknown or unfinished runs exit with code 2.

The report holds verbatim quotes of at most 25 words with deep links. It stays on the owner's
machine and is never committed, pasted into a repository file, a ticket or a chat with an AI tool.

## 5. Session (60 minutes, founder plus owner)

1. 5 min: what Firra is and what it is not (no scores of people, text only, nothing is sent to
   clients).
2. 25 min: lost clients, one at a time. Show the timeline first, hide the detection date. Ask
   "when did you know?". Then reveal: first week at watch or elevated, lead days, the messages
   behind it, the play. Record the human evaluation below per finding.
3. 15 min: retained clients. Show weeks at elevated; for each, "was this a real concern?".
4. 10 min: the play wording and the evidence. Is the quote enough to trust it? What is missing?
5. 5 min: what they would want to see weekly; consent for a second session in two weeks.

## 6. Human evaluation (one row per finding)

| Field | Values |
|---|---|
| Finding | client key, week, state change |
| Founder knew at the time | knew / partly / did not know |
| Timing | on time / would have noticed too late / too early to matter |
| Correct | yes / wrong (reason, one sentence) / cannot tell |
| Evidence enough | yes / no (what is missing) |
| Play useful | yes / no / different play (which) |

Pass for a session: at least one "did not know" or "would have noticed too late", and no "wrong"
without a written reason. Record rows with **client keys only** (A, B, C), never names, quotes or
email addresses, in the design notes; the real mapping stays with the owner. Any "wrong"
becomes a fixture request: describe the pattern in synthetic terms and add it to the Northbeam
spec, never the real text.

## 7. After the session

1. Delete the partner workspace. The in-app purge is P8; until then the owner deletes the
   `workspace` row with the service role in the staging project (child rows cascade, the eval
   tests clean up the same way) and removes the workspace's raw objects from the GCS bucket
   instead of waiting for the purge job or the lifecycle. Verify `source_record`, `evidence`, `extraction`,
   `connection` and `job` rows for the workspace are gone and the object prefix is empty.
2. Revoke the Google OAuth grant (partner side) and delete local report files.
3. Send the partner written confirmation of deletion and the date.
4. Record cost (from `llm_usage`, no content) next to the estimate in the design notes.

## Stop rules

- No DPA or consent: no connection.
- No cost approval: backfill only, extraction off.
- A contributor withdraws: revoke their connection, purge their data, rerun the replay.
- Any finding that depends on an employee's performance rather than a client relationship is out of
  scope (product rule 5); drop it from the report.
