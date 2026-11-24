# Firra — Architecture

**Local v1 (adopted 2026-10-05, `docs/plans/local-v1.md`).** Firra runs on the owner's Mac. This file describes the target. Where a
section still says "Cloud Run", "Cloud Tasks", "KMS", "Vertex", "contributor" or "membership", that part is **legacy code that phase F9
deletes**; the cloud-first original is in the earlier design (not included). Product direction: `docs/north-star.md`; order of work:
`docs/plans/local-v1.md`.
## 1. Components

| Component | Runtime | Responsibility |
|---|---|---|
| `apps/mac` | Native macOS app (Swift/SwiftUI), login item | Menu bar, Today, clients, client detail, replay, onboarding, setup, notifications, routine switches. Holds secrets in the Keychain, **starts and supervises the engine**, talks to it on 127.0.0.1. Connect AI tools screen. |
| Engine (`services/worker`, package `firra`, `firra/local/`) | Python 3.12 child process of the app | Sync (Gmail, Calendar, Slack), clean, resolve, **AI extraction through executors**, verify, memory, signals, assessment, replay, routines, local API (`/v1`-shaped read models and writes), **MCP tools** in two layers, intelligence and client-scoped sources (`docs/plans/local/source-layer.md`; Streamable HTTP handler and stdio entry `firra-mcp`), local scheduler and job executor, retention, backup, diagnostics. |
| Local PostgreSQL 17 + pgvector | child process, **unix socket only** | System of record for the owner's data. Schema = `supabase/migrations` after `supabase/local/shim.sql`. Data directory `~/Library/Application Support/Firra/pg` (0700). |
| Control plane (`apps/api`) | Hono on Cloud Run (EU), one instance for the pilot | MCP **relay** (public `/mcp` ↔ the Mac's outbound WebSocket), **OAuth server** for connectors, device registration, public pages (download, privacy, EULA, consent), release feed. No client data, no content logging. |
| Cloud database | Supabase (EU) | Accounts, devices, OAuth grants only. |
| Release bucket | GCS (EU), public read | DMG, SHA-256, `latest.json`. |
| Owner's AI | Claude Code / Codex CLI on the same Mac, or any MCP client | Does the extraction (executors) and the talking. Not part of Firra. |

**Data at rest.** Mail and chat bodies, mail subjects and meeting titles are encrypted in the local database with the key from the
Keychain (AES-GCM; the record id and the field name are the associated data). The local profile writes no raw provider payloads to
disk (`NoRawStore`); data written by older engines is repaired at start.

**Secrets handshake.** The app reads Google/Slack tokens and the encryption key from the Keychain and hands them to the engine over a unix socket at start
(peer checked). The engine keeps them in memory only. The local API token is generated per start and written to a 0600 file for the app and `firra-mcp`.

**Job execution.** Unchanged in shape: a `job` row (idempotent on `(workspace_id, idempotency_key)`), a handler in `REGISTRY`, retries with backoff up to
`max_attempts`. The local executor in the engine replaces Cloud Tasks; the local scheduler replaces Cloud Scheduler (sweep: sync, processing, routines with
catch-up after sleep, retention, meeting briefs; a clock jump triggers an immediate sweep).

**MCP over a link.** The Mac keeps one outbound WebSocket to the relay (device key pair, signed hello, heartbeat, reconnect with backoff). A call from
claude.ai/ChatGPT arrives at the relay with an OAuth access token; the relay checks it and the device, forwards the call; **the engine validates the token itself**,
applies policy, writes the audit entry, answers from the database; the relay returns the answer and stores nothing. No device online → a clear MCP error
"Firra Mac is offline".

## 2. Data flow

```
connect (Google OAuth on the Mac / Slack token / file import)
  → backfill (3 months default, up to 12) + incremental sync by polling (Gmail history API, Calendar sync tokens, Slack conversations.*)
  → policy & minimization filter (drop non-client data before persisting; Slack: mapped channels and client DMs only)
  → persist source_record (body encrypted in the local database; kept per the retention setting, 12 months by default)
  → clean (threading, quoted-reply/signature/disclaimer stripping; Slack mrkdwn → text)
  → resolve (domain/Slack identity → client, participant → person/stakeholder, alias merge)
  → extract: a packet of records goes to the owner's AI (executor: Claude Code, Codex, or in-conversation tools); JSON comes back
  → verify (quotes must exist verbatim in the cleaned source; schema; policy; else dropped)
  → memory (client card incremental update)
  → signals (rules over mail + Slack + calendar facts, per-source thresholds; weights from config)
  → assessment (state, drivers, caveats incl. participant coverage, play)
  → routines (morning scan, commitment watch) → decision cards (deterministic) → notifications
```

Metadata signals (volume, latency, meetings, renewal dates) need no AI and run on every sync. Content-based signals (complaints, tone,
commitments, goals, needs) appear when extraction has run. Without any AI executor Firra still works on metadata.

## 3. Data model (core)

> Tenancy tables `membership`, `workspace_invite`, `contributor` and the PAT table `mcp_token` belong to the cloud-first design; the local profile has one
> owner and one workspace (RLS stays, harmlessly). They are removed from the local migration set when F9 lands. New in Local v1: Slack `connection.provider`, `device` and
> OAuth grants (control-plane database only).

All tenant tables include `id uuid`, `workspace_id uuid not null`, `created_at`, `updated_at`, RLS enabled.

**Tenancy & access**
- `workspace(name, timezone, default_locale, brief_day, brief_hour, llm_budget_usd_month)`
- `membership(user_id, role[owner|admin])`
- `contributor(email, name, status[invited|active|revoked], token_hash, invited_by)` — connect-only access through a token link; no Supabase account
- `audit_log(actor_type[user|mcp|system], actor_id, action, target_type, target_id, ip, user_agent)`
- `workspace_invite(email, role[admin], token_hash, invited_by, expires_at, accepted_at, accepted_by, revoked_at)` — one-time admin invitations; only the sha256 of the token is stored, the plain token is shown to the inviter once. One pending invite per email; expires after 7 days.

**Integrity.** Child rows reference parents through composite foreign keys `(workspace_id, <parent>_id) → parent(workspace_id, id)`, so a row can never point at another workspace's data, even when written by the service role.

**Policy enforcement.** `set_client_policy` applies changes to stored data immediately and in the same transaction, including everything derived from it. `excluded` deletes the client's records (cascading evidence, extractions and requests), every derived row (commitments, goals, needs, signals, client card, decision cards) and the client's people with their identities; other records that listed a deleted person as participant lose that id. `metadata_only` drops bodies and raw references, retires pending/error records, and deletes all body-derived data (extractions, evidence quotes, memory rows, cards, evidence-based signals) while metadata signals and people stay. The audit entry stores per-table counts only. A `BEFORE INSERT` trigger on `evidence` rejects quotes for a client or record that is not `full` and serializes with the policy change through a row lock, so a processing run racing a policy change cannot leave quotes behind (`ProcessingRepo.save` treats the rejection as "record changed"). RLS on `source_record`/`calendar_event` and on every derived table additionally hides excluded clients; check constraints forbid bodies on `metadata_only` rows and any `excluded` row. P2b tables follow the same rule: `excluded` also deletes the client's assessments (every run), observations and replay-summary entries; `metadata_only` deletes the assessments (recomputed from metadata) and observations learned from bodies, then rebuilds the people's title cache from manual observations. Outcomes are the firm's own facts and stay (hidden while excluded).

**Access model (P0)**
- `authenticated` users only `SELECT`, always filtered by RLS; `anon` has no access. `EXECUTE` on functions is granted explicitly (the default PUBLIC grant is revoked).
- All user writes go through `SECURITY DEFINER` RPCs with an empty `search_path`: `create_workspace`, `create_workspace_invite`, `accept_workspace_invite` (signed-in, confirmed email must match), `revoke_workspace_invite`, `remove_member` (owner only), `list_workspace_members`. Every mutation writes `audit_log`.
- A workspace always keeps ≥ 1 owner (trigger); `audit_log` rejects updates.
- `supabase/tests/00_rls_coverage.test.sql` asserts RLS, a policy, `workspace_id not null`, and no anon/write grants for **every** public table, so new tables are covered automatically.

**Connections**
- `connection(provider[gmail|google_calendar|outlook|outlook_calendar|email_upload], mode[user|org_delegated|org_app|upload], owner_contributor_id null, owner_user_id null, account_label, external_account_id, status[active|error|revoked], scopes text[], token_ciphertext, cursor jsonb, backfill_months, last_synced_at, last_error_class)` — `token_ciphertext` is never readable by users
- `workspace_key(wrapped_dek, kek_name)` — one per workspace; the DEK that encrypts connection tokens, wrapped by Cloud KMS `token-kek`; service role only
- `connection_scope(connection_id, kind[user|channel|project|calendar], external_id, client_id null, include boolean)` — which users/channels/projects are in scope and how they map to clients

**Clients & people**
- `client(name, status[active|churned|prospect], policy[full|metadata_only|excluded], mrr, currency, contract_start, renewal_date, notice_period_days, last_price_increase_at, churned_at, service_tags text[])`
- `client_domain(client_id, domain)` unique per workspace
- `workspace_service(name, description, keywords text[])` — the firm's service catalog, entered by owner/admin; used to match latent needs (F2) and classify job postings (F4)
- `person(client_id null, side[client|firm], display_name, role_title, is_decision_maker bool, is_champion bool, departed_at null, merged_into_person_id null, first_seen_at, last_seen_at)` — an automatic merge sets `merged_into_person_id` (reversible, audited)
- `person_identity(person_id, kind[email|slack_id|pm_id|crm_id], value)` unique per workspace

**Source data**
- `source_record(connection_id, provider, external_id, policy_at_ingest[full|metadata_only], kind[email|chat|call|transcript|task|comment|time_entry|invoice|contract|calendar_event|crm_note], client_id null, thread_key, occurred_at, author_person_id, direction[in|out|internal], participants uuid[], word_count, metadata jsonb, body_clean text null, body_purged_at, raw_object_uri, deep_link, content_hash, processing_state[pending|done|error|skipped_purged|not_applicable], processed_at, processing_version)` unique `(connection_id, external_id)`
- `time_entry(client_id, person_id, role, hours, date, task_ref, source_record_id)`
- `invoice(client_id, amount, currency, issued_at, due_at, paid_at, source_record_id)`
- `contract(client_id, start_date, end_date, renewal_date, notice_period_days, scope_summary, source_record_id)`
- `calendar_event(client_id, starts_at, duration_min, status[scheduled|held|cancelled|rescheduled], attendee_count, source_record_id)`

**Derived**
- `extraction(source_record_id, client_id, item_index, processing_version, kind[request|commitment|praise|complaint|goal|stakeholder_change|in_house_or_competitor_mention|renewal_or_budget_talk|result_reported|tone|initiative], payload jsonb, model, prompt_version)` — `complaint.payload.subtype[delay|quality|communication|other]`; `tone` only for client-authored messages (`payload.tone[positive|neutral|negative]` + quote)
- `evidence(source_record_id, client_id, quote varchar(300), locator jsonb, deep_link)` — quote 3–25 words (DB check), verified; `extraction_evidence` links them, and a deferred trigger rejects an extraction without evidence at commit
- `commitment(client_id, owner_side[client|firm], text, due_date, status[open|done|late|cancelled], stated_at, closed_at, dedupe_key)` + `commitment_evidence` — `closed_at` is set exactly for `done`/`cancelled`, so the status at any past date is known
- `client_goal(client_id, text, metric, stated_at, dedupe_key)` + `client_goal_evidence`
- `request(client_id, origin_kind[request|complaint], text, source_record_id, thread_key, occurred_at, embedding vector(768) null, embedding_model, dedupe_key)` + `request_evidence`
- `external_event(client_id, kind[news|leadership_change|job_post|funding|layoff|m_and_a|filing], title, url, published_at, provider, summary, job_class[insourcing_risk|opportunity|neutral] null, workspace_service_id null, removed_at null)` + evidence quote from the posting for `job_post`
- `latent_need(client_id, topic null, workspace_service_id null, mention_count ≥ 3, thread_count ≥ 2, first_seen_at, last_seen_at, status[open|addressed|dismissed], cluster_key, rule_version)` + `latent_need_request` (the requests its counts come from) + `latent_need_evidence`
- `signal(client_id, type, severity[low|medium|high], occurred_at, rule_version, basis[evidence|metadata], inputs jsonb null, resolved_at null, dedupe_key)` + `signal_evidence` — `metadata` signals store their numeric `inputs` instead of evidence; `tone_decline`, `executive_escalation` and `delivery_complaint` must be `evidence`; `family` (P2b) groups rules for the assessment
- `signal_event(client_id, signal_id, change[opened|severity_changed|reopened|resolved], severity, as_of, rule_version, inputs, job_id)` — append-only history of live runs (P2b)
- `account_assessment(client_id, as_of date, run_kind[live|replay], replay_run_id null, assessment_version, rule_version, risk_state[stable|watch|elevated|high], previous_risk_state, state_since, expansion_state[none|hypothesis|strong], strength[weak|moderate|strong], drivers jsonb, caveats jsonb, data jsonb, play)` + `assessment_evidence`, `assessment_signal` — one live row per client and day; replay rows per `replay_run`
- `person_observation(client_id, person_id, observed_at, source[signature|stakeholder_change|manual], role_title, is_decision_maker, source_record_id null)` — dated role facts; `person.role_title`/`is_decision_maker` are the current-state cache
- `client_card(client_id, version, content jsonb, model, prompt_version)` — account memory, one row per version; evidence ids inside `content` are verified by the worker
- `profit_snapshot(client_id, period, revenue, logged_cost, shadow_hours_low, shadow_hours_high, margin_low, margin_high, inputs jsonb)`
- `decision_card(client_id, week[Monday], kind[risk|margin|pricing|growth|external|commitment|state_change|commitment_watch|expansion], assessment_id, play, facts jsonb, title, interpretation, recommendation, strength, caveats jsonb, body, locale, score (internal), priority, status[new|done|dismissed], outcome_note, inputs jsonb, model, prompt_version, dedupe_key)` + `decision_evidence` — needs evidence unless every fact is a metadata fact
  Routine cards (milestone E, `docs/plans/e-routines.md`) also carry `importance[fyi|attention|important]`, `surfaced_on`, `reason_code`, `demoted_reason[budget]` and `routine_run_id`; the older weekly cards leave them null.
- `decision_feedback(decision_card_id, client_id, user_id, verdict[useful|already_known|wrong|not_now], action[taken|planned|none] null, note)` — one per card and user, through `record_decision_feedback` (moves the card: taken → done; wrong/not_now → dismissed)
- `brief(week, locale, status, delivered_email_at, delivered_slack_at)`
- `meeting_brief(calendar_event_id, client_id, starts_at, locale, content jsonb, status, model, prompt_version, delivered_email_at, delivered_slack_at)` unique `(calendar_event_id, starts_at)` + `meeting_brief_evidence`
- `advice(client_id, requested_by, kind[fix_first|opportunity|renewal_pricing|maintain], body, inputs_hash, model, prompt_version, decision_card_id null)` + `advice_evidence`
- `outcome(client_id, event[renewed|price_increase|downgrade|expanded|churned|referral], occurred_at, notice_at, first_known_at, amount_change, currency, source[user|crm|contract], note, recorded_by)` — through `record_client_outcome`/`delete_client_outcome`; `churned` also marks the client; survives policy changes (hidden while excluded)

**Operations**
- `job(type, status[queued|running|succeeded|failed], idempotency_key, attempts, max_attempts, payload jsonb, progress jsonb, error_class, connection_id null, started_at, finished_at)` unique `(workspace_id, idempotency_key)`
- `llm_usage(job_id, model, input_tokens, output_tokens, cost_usd)`
- `routine(kind[morning_scan|commitment_watch|pre_meeting_brief], enabled, params jsonb, definition_version)` unique `(workspace_id, kind)`, switched through the audited `set_routine_enabled` RPC (owner/admin); `routine_run(routine_id, scheduled_for date, status[queued|running|succeeded|failed], job_id, counts jsonb, error_class, started_at, finished_at)` unique `(routine_id, scheduled_for)`, written by the worker; both readable by workspace members, no user writes
- `replay_run(status[queued|running|succeeded|failed], window_start, window_end, step_days, assessment_version, rule_version, requested_by, job_id, summary jsonb, error_class, started_at, finished_at)` — requested through `request_replay_run`; summary holds client ids, dates and counts only
- `llm_budget_event(month, threshold[50|80|100])` — each budget crossing stored once per workspace and month
- `subscription(provider, external_customer_id, external_subscription_id, plan, status, current_period_end, limits jsonb)`
- `mcp_token(user_id, token_hash, scopes text[], last_used_at, revoked_at)`

## 4. Processing details

> **Local v1 changes to this section** (everything else below stays):
> - **Extraction** is performed by the owner's AI through an executor (`firra/ai/`: Claude Code `claude -p`, Codex `codex exec`, or the MCP tools
>   `get_pending_mail` / `submit_extractions`), never by a model Firra pays for. The prompt `extract.v5` becomes `extract.v6` (chat variant, `topic_key`). The
>   deterministic checks, the retry-once rule and the verified-quote rule are unchanged and now also guard against hostile output: the executor has no tools,
>   no file access and no secrets.
> - **Cards and briefs** (`cards.v1`, `meeting.v1`, "Decision cards", "Pre-meeting brief", "Advise Me") become deterministic renderings of facts
>   (`model = deterministic`); the owner's AI writes the words when asked.
> - **Latent needs** group by `topic_key` instead of embeddings; counts still come from SQL.
> - **Budget, `llm_usage` cost, Vertex** disappear in F9; `llm_usage` keeps model and token counts when the executor reports them.
> - **Retention**: no 72 h / 7-day server rule; bodies live encrypted on the Mac, 12 months by default (setting), `excluded`/`metadata_only` as before.
> - The long **Extraction** paragraph below describes the legacy paid path (`firra/extract/process.py`, `extract.v5`, Vertex); it runs only where the
>   legacy code still exists and disappears in F9. On the owner-AI path the packet goes to an executor (`firra/ai/processor.py`, `extract.v6`) under the
>   AI settings (executor choice, acknowledgement); verification, retry-once and quote rules are the same on both paths.
> - **Slack** is a source (`source_record.kind = chat`, `connection_scope.kind = channel`, `person_identity.kind = slack_id`): see `docs/connectors.md`. Signals
>   count engagement across all channels and use per-source latency thresholds.
> - **Participant coverage**: when the owner appears in few of a client's threads, volume signals are suppressed and a `participant_coverage` caveat is shown.

**Minimization filter (at ingest):** keep a record only if a participant/channel/project maps to a client with policy ≠ `excluded`. Internal-only emails are dropped unless the workspace enables "internal mentions" and the message mentions a mapped client name/domain. For `metadata_only`, persist metadata (participants, timestamps, word counts, durations) and never store body or send it to an LLM.

**Cleaning (at ingest):** `firra/clean` keeps only what the author wrote: quoted history (localized reply headers in en/de/fr/it/sv/nl, Outlook header blocks, `>` lines), signatures, trailing contact blocks, mobile footers, external-sender banners and legal disclaimers never persist; forwarded headers are folded (addresses dropped, forwarded text kept). `word_count` counts the cleaned text, also for `metadata_only` records (which store no body).

**Verification:** `firra/verify` accepts a model quote only as a contiguous, case-sensitive match in the cleaned body after NFKC, quote-mark, invisible-character and whitespace normalization, with 3–25 words and ≤ 300 characters. Rejected quotes are dropped and counted by reason; the stored quote is the matching source span, the locator its code point offsets.

**Entity resolution:** deterministic first (exact email, domain, Slack user id, PM user id); fuzzy/LLM only for ambiguous merges, logged in `audit_log` and reversible in UI.

**Extraction:** off unless `FIRRA_PROCESSING_ENABLED` is true (Terraform `processing_enabled`; every record is a paid model call). `process.records` (queue `extract`) drains a workspace's `pending` records in batches of 20, one job per workspace at a time; it is requested after each ingest task and by the 15-minute sweep. Ingest marks new or changed `full` emails `pending`; everything else is `not_applicable`. Per record: re-clean, skip duplicates (same cleaned text already processed in the thread), one Gemini call (prompt `extract.v5`, up to 2 earlier thread messages as unquotable context, structured output; a `need` item, a client's statement of need without an ask, is stored as a `request` with `payload.form = need`, so the request memory and the latent-need engine are unchanged), deterministic checks (verified quote, kind fields, tone and `initiative` only for client-authored messages, neutral tone dropped, `renewal_or_budget_talk` needs `budget_direction` grow|cut|renewal|neutral, `initiative` needs `initiative_type`), then the record's extractions are replaced in one transaction (`processing_version` + 1); evidence keeps the row of every quote that is still present, because commitments, signals and cards cite evidence by id, and only quotes the new run no longer produces are removed. Invalid output after the retry or a blocked answer → `error`; budget reached → job fails with `budget_exceeded`, records stay `pending`. The daily purge never waits: pending bodies it removes become `skipped_purged` and are counted. Quotes stay in the original language; `text` is a one-sentence English statement of the item.

**Memory:** when processing drains the workspace, `memory.rebuild` refreshes non-excluded clients in one transaction each. It derives commitments, goals, requests and results only from verified extractions and links their evidence; repeated request quotes collapse per client, while completed/cancelled commitments retain their status. Client-authored email signatures supply a validated role title before ingest discards them. Champion selection uses inbound message counts and a versioned rule config. A new `client_card` version is stored only when its facts change; `metadata_only` cards contain stakeholders, timeline and unknowns without body-derived facts.

**Signals (rules and weights in `services/worker/config/signals.yaml`, engine in `firra/signals`):** deterministic rules over SQL-loaded facts (`signals.evaluate`, no model calls). Each rule returns drafts; the engine drops any draft that breaks an invariant (evidence signals cite verified evidence of the client, metadata signals store numeric `inputs`, tone/escalation/delivery-complaint signals are never metadata-only); storage upserts on `(client, type, dedupe_key)`, replaces evidence links and resolves signals a run no longer produces. `excluded` clients and prospects are never evaluated; churned clients are evaluated at their churn date (calibration only). `config.version` is stored as `rule_version`. Rule catalog (windows count back from the reference time `ref`):

**Facts as of a date (P2b D5):** `SignalRepo.load_facts(ref)` returns what was knowable at `ref`, so live runs, replays and backtests share one loader and cannot see the future: client-side people with `first_seen_at ≤ ref` (a later `departed_at` is not known yet); decision makers from dated `person_observation` rows ≤ `ref` (signature titles written at ingest, `stakeholder_change` items written by memory; a `manual` observation counts from `first_seen_at`), never from the `person` cache; the champion recomputed with the memory rule over client messages before `ref`; commitment status from `closed_at` with `late` recomputed against `ref`; open or addressed latent needs with `last_seen_at ≤ ref`. Facts also carry each record's connection, meeting attendee counts and, per connection that carried the client's records in days 31–120, `{status, synced_until}` for the D4 coverage guard — the one input that uses current sync state, because it describes how complete the stored data is. An integration property test adds records, people, observations, closures, events and needs after `ref` and asserts identical facts. Writers of a client's derived rows lock the client row first (same order as `set_client_policy`) and skip writes the policy no longer allows.

**Account assessment (P2b D4, D6, D9):** `signals.evaluate` runs the rules, then the coverage guard (a baseline connection whose data ends more than `coverage.tolerance_days` (3) before the run suppresses every `uses_volume` rule and adds the `coverage_changed` caveat), then `firra/assessment` for each active client: per-family levels (a family counts once), risk `stable | watch | elevated | high` (one family high or two at medium → high; one medium plus another family, or three families at low → elevated), the renewal timing modifier, a holiday cap on engagement/volume drivers, hysteresis (escalate at once, de-escalate after 2 consecutive lower runs), expansion `none | hypothesis | strong`, ordered drivers with up to 3 primary risk drivers, strength, caveats (`coverage_changed`, `low_data`, `holiday_period`, `recent_positive`, `stakeholder_handover`, `metadata_only`) and a play from `config/plays.yaml` (`fix_first` also on an unresolved complaint, D7). Config in `config/assessment.yaml` (`assessment.v1`). One transaction per client writes the signals, a `signal_event` per change (opened, severity_changed, reopened, resolved) and the day's live `account_assessment` with its signal and evidence links; the replay runner reuses the same pure `assess`.

**Churn Replay (P2b D8):** a `replay_run` (requested through the audited `request_replay_run` RPC, or created by the local CLI `python -m firra.assessment replay --workspace --from --to [--step 7]`) is executed by task `assessment.replay {replay_run_id}` (queue `process`, no model calls). For every active or churned, non-excluded client and every step from `window_start` to `window_end` (a churned client stops at its churn date) it builds leak-free facts, runs the rules in memory, applies the coverage guard and `assess` with the previous step as the series' state, and writes `account_assessment` rows with `run_kind = replay`; it never writes signals. A rerun deletes the run's rows first. The summary (`replay_run.summary`, ids, dates and numbers only) gives per client the detection date (first step at `elevated`/`high` whose next step is too), lead days before the churn's notice date (else the churn date), max state, elevated weeks, flicker and a miss reason (`late`, `coverage_changed`, `low_data`, `no_signal`), plus totals (losses detected ≥ 30 days ahead, median lead, elevated weeks on retained clients per 10 clients per month, flicker). `python -m firra.assessment report` turns a finished run into a Markdown report per client (state changes, the new drivers at each change, verified quotes, the play) plus an overview with retained-client false alarms.

Notation: *client message* = record with `direction = in` whose author is a client-side person.
*Complaint* = `complaint` extraction on a client message. Times are UTC. "Within N d" means
`ref − N days < t ≤ ref`; "days a–b" means `ref − b days < t ≤ ref − (a − 1) days` (so "days 31–120"
starts where "within 30 d" ends); "due within days 1–45 before `ref`" means
`ref − 45 d ≤ due ≤ ref − 1 d`; "within the next N d" means `ref < t ≤ ref + N days`. Events or records
after `ref` never count. Severity defaults below live in `signals.yaml`.

| Type | Fires when | Basis / evidence | dedupe_key | Severity |
|---|---|---|---|---|
| `delivery_complaint` | ≥ 1 complaint with subtype `delay`, `quality` or `communication` within 30 d | evidence: those complaints' quotes (newest 5) | subtype | medium; high if ≥ 2 of that subtype |
| `repeated_complaint` | ≥ 2 complaints (any subtype) on ≥ 2 distinct records within 60 d | evidence: newest 5 | `repeated` | high |
| `executive_escalation` | a complaint within 30 d authored by a decision maker, **or** on a thread where a decision maker whose `first_seen_at` is within 30 d of that complaint also wrote a client message | evidence: the complaint(s) | `escalation` | high |
| `tone_decline` *(reference)* | processed client messages within 30 d ≥ 5, share₃₀ = negative-tone items / those messages ≥ 0.30, and share₃₀ − share_base ≥ 0.20, where share_base covers days 31–120 (0 when that window has no processed client messages) | evidence: negative tone quotes within 30 d (newest 5); inputs `{share_30, share_base, messages_30}` | `tone` | medium; high if share₃₀ ≥ 0.50 |
| `champion_departure` | the champion has `departed_at` within 90 d | evidence: the matching `stakeholder_change` `left` quote if present, else metadata with inputs `{person_id, departed_at}` | person id | high |
| `champion_latency_drift` | champion replies (client message whose previous record in the thread is firm-authored): ≥ 2 within 60 d with median gap ≥ 24 h and ≥ 2 × the median of ≥ 3 replies in days 61–240 | metadata: `{recent_median_h, base_median_h, recent_n, base_n}` | person id | medium |
| `new_senior_stakeholder` | a client-side decision maker with `first_seen_at` within 60 d, on a client whose `contract_start` is > 90 d before `ref` and whose earliest loaded record is ≥ 60 d before that `first_seen_at` (P2b amendment) | evidence: `stakeholder_change` `joined`/`new_role` quote naming them if present, else metadata `{person_id, first_seen_at}` | person id | medium |
| `reduced_engagement` *(reference)* | baseline = client messages in days 31–120 ÷ 3 ≥ 4, and client messages within 30 d ≤ 0.5 × baseline | metadata: `{messages_30, baseline_per_30}` | `volume` | medium; high if messages_30 = 0 |
| `meeting_cancellations` | calendar events starting within 60 d (none after `ref`): ≥ 2 with status `cancelled`/`rescheduled` and that share ≥ 0.40 | metadata: `{affected, total}` | `meetings` | medium |
| `late_commitment` | a firm-side commitment, status `open`/`late`, due within days 1–45 before `ref` | evidence: the commitment's evidence | commitment id | medium; high if ≥ 14 d past due |
| `goal_result_mismatch` | a `result_reported` paired with the most recent `goal` before it (≤ 180 d earlier); *N_g*, *N_r* = first number in each item's `payload.text` (commas removed, `%` ignored); the goal text has none of `under`, `below`, `less`, `lower`, `max`, `reduce`; and *N_r* < 0.5 × *N_g* | evidence: both quotes; inputs `{goal, result}` | goal's record id | medium |
| `first_90_days_no_result` | `contract_start` 90–150 d before `ref` and no `result_reported` since `contract_start` | metadata: `{contract_start, days}` | `onboarding` | medium |
| `in_house_risk` | an `in_house_or_competitor_mention` within 90 d | evidence: newest 5 quotes | `in_house` | medium |
| `renewal_window` | `renewal_date` within the next 90 d, or the notice deadline (`renewal_date − notice_period_days`) within the next 30 d | metadata: `{renewal_date, notice_deadline, days_to_notice}` | renewal date (ISO) | medium; high if `days_to_notice` ≤ 14 (including a deadline already passed) |

Not in P2 (need later data): payment delay drift (P5), external job posts, leadership changes, funding/layoffs/M&A (P6). The executable form of the catalog is `services/worker/tests/unit/signals/oracle.yaml`.

**Signals across mail and Slack (`signals.v3`, L2S-09, spec `docs/plans/local/multichannel-signals.md`):** `RecordFact.source` (`email`|`chat`); volume rules count client **touches** (chat records collapsed into bursts of at most 30 minutes); `tone_decline` reads email only; reply latency is evaluated per source (chat in working hours of the workspace time zone, keys `latency:chat` and `<person>:chat`); the guard stage also suppresses volume rules and adds the caveat `participant_coverage` when the owner is not on most firm-side threads with the client (`partial_view` in `signals.yaml`). The loader keys chat records as `<channel_id>/<thread_ts>` (the Slack connector stores `channel_id` in `source_record.metadata`) and finds the owner through the identity of the active Gmail connection's mailbox or the Slack connection's user id (`connection.external_account_id`).

**Risk signals (F3):** delivery complaint (by complaint subtype), executive escalation (complaint by a decision maker, or a new senior stakeholder on a complaint thread), tone decline (share of negative `tone` extractions in the client's messages over the last 30 days vs. the account's 90-day baseline), reduced engagement (message volume, client reply latency, cancellations vs. baseline; metadata only, so it also covers `metadata_only` clients). Tone is text-only, aggregated per account and shown as events with quotes — never a per-person emotion score (product rule 4).

**Latent demand (F2):** `request` and `complaint` items are embedded and grouped into topics per client. A topic becomes a `latent_need` at ≥ 3 distinct mentions in ≥ 2 threads within 60 days (thresholds in `config/signals.yaml`; the same quote in replies counts once). Counts come from SQL; the LLM only names the topic from its quotes. A topic matching a `workspace_service` is an expansion opportunity, unless the client already has that service (`client.service_tags` holds catalog service names; such a need stays visible but raises no expansion signal); otherwise an unmet need. Cross-client clustering of the same embeddings is package discovery (P5).

`needs.update` runs after `memory.rebuild` when paid processing is enabled. It embeds only unembedded requests of `full`-policy clients, greedily joins each request to the nearest cluster centroid in time order, and chooses the largest qualifying sliding window (latest on ties). A per-client transaction replaces open needs and their request/evidence links, then writes exact mention and distinct-thread counts from SQL over those links. Addressed and dismissed needs remain untouched. Service descriptions are embedded once per run only when a qualifying need exists, for centroid matching; topic naming is deferred.

**Pre-meeting brief (F1):** the 15-minute sweep finds client `calendar_event`s starting within the workspace lead time (default 2 h) and enqueues one task per `(calendar_event_id, starts_at)`; a rescheduled meeting gets a new brief, a cancelled one none. Facts are assembled deterministically: records since the last held meeting, open commitments per side, unresolved complaints, stakeholder changes, tone signals, `renewal_or_budget_talk` and out-of-scope requests, external events (P6), and a "we don't know" checklist of missing or stale client-card fields. The LLM writes the text and 3 questions, each tied to the unknown or open item behind it; the result must pass `verify`. Recipients: owner/admin, for every client meeting. `metadata_only` clients get counts and external events only.

**Advise Me (F5):** on demand from the client page. A deterministic gate picks the kind: `fix_first` when a high-severity risk signal, late commitment or unresolved complaint is open; else `opportunity` (latent need, opportunity job post, expansion signal); else `renewal_pricing` (renewal within 90 days); else `maintain`. The LLM words one recommendation from the selected facts only; numbers come from profit tables; it must pass `verify`. Cached by `inputs_hash`, limited to 20 requests per workspace per day, audited, and can be saved as a decision card.

**Profit (deterministic):**
- margin = revenue − logged_hours × role_cost − shadow_hours × blended_cost, reported as a range.
- shadow hours estimated from message word counts (write/read constants) and meeting durations × firm attendees; constants in `config/costs.yaml`, editable per workspace.
- revenue at risk (90d) = Σ MRR × risk weight by signal severity.
- renewal pack: delivered work, client-stated results (quotes), margin, relationship state, suggested increase range by rule.
- proposal check: compare new proposal's estimates with actual hours of similar past engagements (embedding + service tags).
- package discovery: cluster `request` embeddings across clients; LLM names clusters; show frequency and hours spent.

**Growth:** referral/case-study moment (recent explicit praise of results + healthy signals), expansion signals (needs mentioned that match firm services, client growth events), ICP report (which client types stay longest and have best margin).

**Decision cards:** generated weekly per workspace and immediately for high-severity signals (if enabled). LLM writes text; numbers come from profit tables; every card must pass `verify`.

**Localization:** the Mac UI is English only in v1. Generated text (cards, brief) is produced in the recipient's locale; evidence quotes remain in the original language.


## 5. Capacity targets (v1)

One owner per install. Backfill of 3 months (about 4,000 mapped records) completes in the background within hours, bounded by the owner's AI limits;
metadata signals are available minutes after the first sync. Incremental latency: new mail or Slack message seen within about 5 minutes while the Mac is awake;
after sleep the next sweep catches up. The relay is sized for hundreds of Macs on one instance for the pilot.

## 6. Cost controls

Firra pays for no model. Cloud cost is one small Cloud Run service, a Supabase free/small project for accounts, and a bucket. The owner's AI usage is on the
owner's own subscription: process newest and active clients first, 3 months by default, pause until a limit resets, show progress. Packets per thread or
channel day keep Slack from eating the subscription.

## 7. Infrastructure as code (Terraform, `infra/terraform`)

After phase F9: Cloud Run service for `apps/api` (relay and control plane), Secret Manager, Artifact Registry, release bucket (public read), Workload Identity
Federation (kept for manual runs), budget alerts. The worker service, Cloud Tasks queues, Scheduler, Pub/Sub, KMS, data buckets are deleted (L9-02).

## 8. Backups & recovery

Most local state is **recoverable**: resync from Gmail/Slack and re-extract (hours, on the owner's AI subscription). **Non-recoverable** state: feedback verdicts,
outcomes, client/domain/channel mappings, policies, the service catalog, routine settings. `POST /local/backup/create` (`firra/local/routes/maintenance.py`,
backed by `firra/backup/service.py`) returns it as versioned JSON encrypted with a key derived from a recovery phrase (scrypt + AES-GCM); the app saves the
blob through its save panel, and `POST /local/backup/restore` restores it into an empty workspace before resyncing. Time Machine covers the rest if enabled.
An automatic backup before each app upgrade migrates the database is **planned, not yet implemented** (no code path calls backup from the upgrade/migrate
path). Deletion: "delete client" (the `excluded` path) and "delete everything" (data directory, Keychain items, token revoke).
