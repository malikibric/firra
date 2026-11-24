# Design-partner onboarding (milestone E) — operator checklist

> **Local v1 (2026-10-05) supersedes this flow:** one owner on their Mac, no contributors/teams; the
> owner's own AI (Claude Code / Codex / in-conversation) does extraction on their subscription, not
> Vertex; bodies stay encrypted on the Mac (12 months default), not 72 h / 7-day server retention.
> Kept until rewritten for the local pilot; do not follow the contributor, Vertex-cost or GCS steps below.

Order matters. One workspace per partner, never shared, so deleting it deletes everything
(`docs/runbooks/design-partner-pilot.md:34-35`). Every claim cites file:line; assumptions and unknowns
are in **Open questions**. Never copy partner content into a repo, ticket or chat (`CLAUDE.md:254`;
`design-partner-pilot.md:86-87`).

## 0. Agree before anything touches real mail

- **DPA**: counsel-reviewed; the legal drafts (not included) is the engineering draft with the status of
  controls (`:149-155`). No DPA or consent -> do not connect (`design-partner-pilot.md:130`).
- **Clients in scope**: written consent naming clients by key (A, B, C), not name
  (`design-partner-pilot.md:15`). Set each policy before connecting: `full` (bodies read),
  `metadata_only` (no bodies, no quotes) or `excluded` (nothing stored; deletes what exists) —
  `docs/architecture.md:58`.
- **Mailboxes in scope**: list the addresses and their owners (`docs/runbooks/mailbox-connections.md:28-35`).
- **Retention agreed**: bodies deleted once processed and delivered to the owner's Mac, 72 h after
  ingest if uncollected, never later than 7 days (`architecture.md:29`;
  `services/worker/firra/db/retention.py:21-22`; the legal drafts (not included)`).
- **Cost**: a written owner go with the estimate from section 3, never implied (`docs/roadmap.md:86`;
  `design-partner-pilot.md:18`).

## 1. Google access: test users and the consent screen

`gmail.readonly` is restricted: until Google verification + CASA the app stays in **Testing**, which
lists up to 100 test users by hand (`mailbox-connections.md:25-26`; `docs/connectors.md:38`).

1. GCP Auth Platform -> Audience -> **Testing** -> **Test users**: add every address that will connect,
   including contributors (`mailbox-connections.md:28-35`).
2. The app requests exactly (`apps/api/src/connect/google.ts:9-11`): `openid`, `email`,
   `https://www.googleapis.com/auth/gmail.readonly`,
   `https://www.googleapis.com/auth/calendar.readonly`; with `access_type=offline`, `prompt=consent`,
   `include_granted_scopes=true` (`google.ts:29-31`) — hence a consent screen every time and a refresh
   token.
3. **What the screen asks** (Google's per-scope descriptions — confirm in the console): view your email
   messages and settings; view your calendars; see your primary Google Account email address; plus the
   "Google hasn't verified this app" warning, and a Workspace admin can block it
   (`design-partner-pilot.md:32`).
4. Refresh tokens issued in Testing expire after 7 days: connect no more than a day or two before the
   session, and expect "token revoked — reconnect" (`design-partner-pilot.md:29-31`;
   `dpa-draft.md:138`). Not a test user -> "Access blocked: app has not completed verification"; not
   wired -> `connect_error=google_not_configured` (`mailbox-connections.md:144-145`).

## 2. Connect from the Mac app

1. Sign in, then **Connections -> Connect my mailbox** and pick the provider (`ConnectionsView.swift:15-19`).
   The model opens the API's authorize URL in the system web-auth sheet with the `firra://` callback
   (`ViewModels.swift:398-409`); the API exchanges the code (`google.ts:70`).
2. Expect "Mailbox connected" and "imported X of Y" (`mailbox-connections.md:66-68`;
   `ConnectionsView.swift:77-79`); the 12-month backfill runs chunked per connection x month
   (`architecture.md:12,67`).
3. Contributors connect their own from **Team sources -> Create link**
   (`mailbox-connections.md:134-138`).

## 3. Backfill and its cost (with extraction off)

Extraction is the only paid step and runs only when `FIRRA_PROCESSING_ENABLED=true`, default **false**
(`architecture.md:126`; `services/worker/firra/settings.py:69`;
`infra/terraform/modules/firra_env/variables.tf:157-161`).

1. Let the backfill finish, then count and estimate without content:
   `uv run python -m firra.assessment estimate --workspace <uuid> --months 12`
   (`services/worker/firra/assessment/__main__.py:9-10,87-91`).
2. Formula — default extract model `gemini-3.5-flash-lite` (`variables.tf:150-152`), non-global Vertex
   list prices, 10 % above global (`services/worker/firra/llm/prices.py:3-5`):

   ```
   email_usd   = (input_tokens * 0.33 + output_tokens * 2.75) / 1_000_000   # prices.py:27,41-46
   mailbox_usd = client_matched_full_emails * email_usd * headroom          # headroom 1.3
   ```

3. **Assumptions (unverified — see Open questions)**: ~1.8k input, ~300 output tokens per full email
   (the design notes); embeddings 0.15 USD/1M input, under 0.01 USD per mailbox
   (`prices.py:33`; the design notes). With those, `email_usd ~= 0.00142`; 4,000 client-matched
   emails ~= 5.7 USD, ~= 7.4 USD with headroom.
4. Send the owner the count and estimate, set `FIRRA_LLM_HARD_CAP_USD` to the approved amount
   (`settings.py:63`), then flip `processing_enabled` (`variables.tf:157-161`;
   `infra/terraform/modules/firra_env/cloud_run.tf:54`). At the cap the job fails with
   `budget_exceeded` and records stay `pending` (`architecture.md:126`).

## 4. When the owner must give the paid-run go

- Every paid run needs an explicit go for that run, never implied (`docs/roadmap.md:201`; R-D7).
- The partner backfill extraction cost is approved per mailbox before it runs (`roadmap.md:86`).
- The 15 USD staging cap is the corpus/eval budget O-D4 (`roadmap.md:21-22`) and still needs a go per run
  (`docs/runbooks/evals.md:149`); it is not a blanket allowance for partner extraction (`design-partner-pilot.md:60-61`).
- No go: backfill only, `FIRRA_PROCESSING_ENABLED=false` (`design-partner-pilot.md:131`).

## 5. First-day checks (counts only)

```sql
select kind, processing_state, count(*) from public.source_record
where workspace_id = '<workspace_id>'::uuid group by 1, 2 order by 1, 2;
select count(*) as records, count(*) filter (where body_clean is null) as body_gone,
       count(*) filter (where vault_delivered_at is not null) as delivered
from public.source_record where workspace_id = '<workspace_id>'::uuid;
select provider, status, backfill_months, count(*), max(last_synced_at) from public.connection
where workspace_id = '<workspace_id>'::uuid group by 1, 2, 3 order by 1;
select type, status, error_class, count(*) from public.job
where workspace_id = '<workspace_id>'::uuid group by 1, 2, 3 order by 1, 2;
select coalesce(sum(u.cost_usd), 0) as usd, count(*) as calls from public.llm_usage u
join public.job j on j.id = u.job_id where j.workspace_id = '<workspace_id>'::uuid;
select (select count(*) from public.extraction where workspace_id = '<workspace_id>'::uuid) as extractions,
       (select count(*) from public.evidence where workspace_id = '<workspace_id>'::uuid) as evidence,
       (select count(*) from public.decision_card where workspace_id = '<workspace_id>'::uuid) as cards;
```

Expect records only for client-mapped mail (`architecture.md:118`), `body_gone` rising (`retention.py:36-59`),
one `active` connection per mailbox, no growing `error_class`, and `usd` inside the approved estimate.

## 6. If the partner asks what Firra stores

- Only mail involving a mapped client; internal-only mail is dropped unless internal mentions are
  enabled (`architecture.md:118`).
- Bodies are temporary: deleted when processed and delivered to the Mac, 72 h if uncollected, 7 days at
  most (`architecture.md:29`; `dpa-draft.md:114`).
- What stays: metadata, one-sentence extractions, verified quotes of at most 25 words with deep links,
  the client card, signals and assessments (`architecture.md:79,87`; `dpa-draft.md:115`); kept for the
  term and deleted on request (`dpa-draft.md:115,122`).
- Per-client control is real (`full` / `metadata_only` / `excluded`, `architecture.md:58`); OAuth
  tokens are encrypted with a per-workspace key (`architecture.md:67-68`; `dpa-draft.md:118`).
- EU-only storage and compute; paid Vertex AI EU endpoints only, no model training on customer data; never
  stored: logs with bodies/quotes/addresses (`CLAUDE.md:138`), audio/video/emotion, employee scoring, auto-send (`CLAUDE.md:43-45`; `dpa-draft.md:86-88,100-106`).

## 7. Delete everything

1. One client: `select public.set_client_policy('<client_id>', 'excluded')` deletes its records, derived
   rows and people in the same transaction
   (`supabase/migrations/20261002000019_replay_narrative.sql:37`; `architecture.md:58`).
2. Whole partner: disconnect each mailbox in the app (`ConnectionsView.swift:62-66`), delete the
   `workspace` row with the service role (children cascade), delete the workspace's GCS prefix, and
   verify `source_record`, `evidence`, `extraction`, `connection` and `job` counts are zero
   (`design-partner-pilot.md:119-123`).
3. Bodies are already gone: hourly purge with the 72 h / 7 day rules (`retention.py:36-59`;
   `services/worker/firra/maintenance/router.py:24-39`), with the GCS 7-day lifecycle as backstop
   (`infra/terraform/modules/firra_env/storage.tf:29`; `architecture.md:17`). No in-app purge screen yet
   (P8) (`design-partner-pilot.md:119`).
4. Partner side: revoke Firra at myaccount.google.com/permissions, delete local reports, and send
   written confirmation with the date (`design-partner-pilot.md:124-125`).

## Open questions

- Exact consent wording and the current test-user cap: confirm in the Google console at setup (the
  per-scope text is Google's, not ours).
- Token assumptions (~1.8k in / 300 out) come from the v2-era T15 plan (the design notes); the
  owner-AI path uses `extract.v6` (`services/worker/firra/ai/processor.py:40`), the legacy paid path
  pins `extract.v5` (`services/worker/firra/extract/process.py:27`); and the plan's 0.30/2.50
  rates are below `prices.py:27`. Re-measure from the first mailbox's `llm_usage`; whether a partner run
  counts against O-D4's 15 USD or is always separate is also open (`roadmap.md:21-22` vs `:86`).
- `architecture.md` no longer says `extract.v4` (the legacy paragraph now says `extract.v5` and is
  marked legacy) — drift closed.
