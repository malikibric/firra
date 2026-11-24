# Milestone C: Firra API `/v1` (plan)

> **Cloud-first history.** The order of work is now `docs/plans/local-v1.md`.


Roadmap C (`docs/roadmap.md`), tasks T29–T34 and T14 (the original task spec). Written 2026-10-02.
Owner direction the same day: work autonomously, no Apple Developer account (so D ships as an
ad-hoc signed DMG later, after MCP), no new spend. This plan adds **no migration** (T32 deferred) and
**no new dependency**.

Goal (roadmap exit): the Mac app and the MCP server can be built against `/v1` without new backend
logic. Every judgement is already computed and stored by the worker (R-D2); `/v1` only reads
stored rows and records feedback and outcomes.

## Decisions

| # | Decision | Why |
|---|---|---|
| C-D1 | Reads run **as the caller**: a per-request Supabase client built from the publishable key and the caller's access token, so Postgres RLS (including `excluded` hiding and the `metadata_only` rules from migration 16) is the authorization layer. The service role is used only for the membership check and the audit write. | One enforcement point that already has pgTAP tests; a bug in route code cannot widen access. |
| C-D2 | `SUPABASE_PUBLISHABLE_KEY` becomes an **optional** env of `apps/api` (public key, not a secret). Data routes answer `503 reads_not_configured` when it is unset. Terraform adds it to the api service env (variable already exists). | Deploy order lesson: Terraform first, code requiring it second. |
| C-D3 | Authorization: valid user token, then membership role `owner` or `admin` in `:workspaceId` (existing `requireWorkspaceRole`). Contributors never reach `/v1` data. Unknown or foreign workspace, client or card is `404 not_found` (RLS returns no row) so existence does not leak. | CLAUDE §3. |
| C-D4 | **Audit on every read** of client data: one `audit_log` row per request (`actor_type` user, action `read.<resource>`, target client when there is one, metadata: route name and result count only). The audit write happens before the response is sent and **fails closed** (500, no data) if it cannot be written. | CLAUDE §3: every read is audited. |
| C-D5 | Read access sits behind a `ReadStore` interface (`list/get` methods per read model). The first implementation is the RLS-as-caller one. MCP personal access tokens (milestone F) are not Supabase sessions; F adds a second implementation that re-applies the same visibility rules in SQL functions, behind the same interface and the same contract tests. | Keeps C-D1 now and leaves F open without rewriting routes. |
| C-D6 | Response shapes are zod schemas in `packages/contracts` (`src/v1/*.ts`); the OpenAPI 3.1 document is generated from them with zod's built-in `z.toJSONSchema` (no new dependency) and served at `GET /v1/openapi.json` (authenticated, no data). The Mac client and MCP tool schemas are generated from it. | "OpenAPI from `packages/contracts`", one source of truth. |
| C-D7 | Error model: JSON `{ "error": "<code>", "message": "<short>", "request_id": "<id>" }`; codes: `unauthorized` 401, `forbidden` 403, `not_found` 404, `invalid_request` 422, `rate_limited` 429, `reads_not_configured` 503, `internal_error` 500. Messages never contain row data. Every response carries `X-Request-Id` and `Cache-Control: no-store`. | Stable contract for generated clients. |
| C-D8 | Versioning: additive changes only inside `/v1` (new optional fields, new endpoints); a breaking change is `/v2`. Unknown enum values must be tolerated by clients. | Mac app updates by manual DMG: old clients must keep working. |
| C-D9 | Lists are cursor-paginated (`limit` ≤ 100, opaque `cursor`) where unbounded (timeline, evidence, outcomes); the attention list is bounded by design (≤ 150 clients per workspace). | Predictable cost. |
| C-D10 | Quotes: `metadata_only` and `excluded` clients return no quotes (RLS plus a contract test per endpoint); evidence objects carry `quote`, `locator`, `deep_link`, source date, author display name and role only for client-side people, subject trimmed to 120 characters, as in the replay report. | Product rules 2 and 8. |
| C-D11 | Rate limiting: keep the existing per-IP limiter on `/v1/*`, add a per-user limiter (default 300/min) after authentication. | Cloud Run has no shared limiter yet (P9). |

## Endpoints (milestone C)

All under `/v1/workspaces/:workspaceId`, owner/admin only, audited.

| Endpoint | Content | Task |
|---|---|---|
| `GET /changes?since=YYYY-MM-DD` | workspace-wide live risk/expansion transitions and signal changes, newest first, at most 300; defaults to 7 days, at most 31 days | T90 |
| `GET /attention` | clients ordered by need: latest live assessment per client with risk state, previous state and since-date, expansion state, play, strength, top two drivers (labels), days to notice | T29 (first endpoint), T30 |
| `GET /clients` | all non-excluded clients with name, status, policy, latest state | T30 — implemented; CI integration pending |
| `GET /clients/:clientId` | client detail: state and history of changes, drivers with evidence ids, caveats, play, open commitments, stakeholders, unknowns | T30/T91 — implemented; champion, stored unknowns and up to five newest claim evidence links |
| `GET /clients/:clientId/timeline` | dated events (state changes, signals, commitments) | T31 |
| `GET /clients/:clientId/evidence` and `GET /evidence/:evidenceId` | verified quotes with context | T31 |
| `GET /replay/runs`, `GET /replay/runs/:runId/clients/:clientId` | replay summary and the stored narrative used by the report | T31b — implemented; CI integration pending |
| `GET /decisions` | decision cards with kind, evidence ids, feedback state; optional `importance` (fyi, attention, important) and `kind` filters, `importance` and `surfaced_on` on routine cards | T33, T47e |
| `POST /decisions/:cardId/feedback` | `record_decision_feedback` RPC as the caller | T33 |
| `POST /clients/:clientId/outcomes`, `DELETE /outcomes/:outcomeId` | `record_client_outcome`, `delete_client_outcome` | T33 |
| `GET /routines` | three first-party kinds in fixed order, lazy defaults and newest scheduled run; audited owner/admin caller read | T50e |
| `PUT /routines/:kind` | enable/disable through `set_routine_enabled` as caller; one RPC audit | T50e |
| `GET /learning` | precision, lead time, usefulness per family and play | T34 |

## Tasks

| ID | Task | Owner | Notes |
|---|---|---|---|
| T29 | Skeleton: env, per-request RLS client, `ReadStore`, audit-on-read helper (fail closed), error model, request id, per-user limiter, contracts + OpenAPI generation, `GET /attention`; Terraform env line | Claude | ⚠ tenancy; integration tests in CI (Supabase) |
| T30 | Read models: attention (complete), clients, client detail | Codex | after T29 |
| T31 | Read models: timeline, evidence | Codex | after T30 |
| T31b | Worker persists the replay narrative in `replay_run.summary` (no migration); `/v1` replay endpoints | Codex | after T31 (R-D2: the app never re-implements the report logic) |
| T32 | Surfaced items as `decision_card` kinds (migration + pgTAP) | Claude | **deferred** (2026-10-02): weekly cards already carry feedback; replay findings are rated by hand in `status-b.md`; revisit when a design partner asks for in-app feedback on state changes or replay findings. Milestone C therefore has no migration |
| T33 | Decisions list, feedback and outcome endpoints (RPCs as the caller) | Codex | after T31b |
| T34 | Learning metrics in the worker + `/learning` | Codex | after T33 |
| T14 | Adversarial policy tests through every read model | Codex | after T30, T31 |

## Tests (every endpoint)

- Contract: response validates against its zod schema; error shapes.
- Isolation: a user of workspace A gets 403/404 for workspace B ids; a contributor gets 403.
- Policy: `excluded` client absent everywhere; `metadata_only` client has no quote anywhere.
- Audit: one row per read with the right actor and action; a failing audit write returns 500 and
  no data.
- No data in logs: request log fields stay ids, counts, timings.
- Unit tests use an in-memory `ReadStore`; the RLS implementation is covered by integration tests
  against local Supabase in CI (same as the existing `apps/api` integration tests).

## Out of scope

Writes other than feedback and outcomes, setup endpoints (D), MCP (F), webhooks, billing, any
schema change besides T32, any new dependency.

## Deploy notes (T29)

- Terraform (`infra/terraform/modules/firra_env/cloud_run.tf`) now gives the api service
  `SUPABASE_PUBLISHABLE_KEY` (the variable already exists for web). **Apply Terraform before
  expecting data from `/v1`**; until then the data routes answer `503 reads_not_configured` and
  nothing else changes.
- New optional env: `SUPABASE_PUBLISHABLE_KEY`, `USER_RATE_LIMIT_PER_MINUTE` (default 300);
  both documented in `.env.example`.
- CI integration job already exports `SUPABASE_PUBLISHABLE_KEY`; `apps/api/test/integration/read.test.ts`
  proves RLS-as-caller on local Supabase (excluded client hidden, outsider refused, audit row).
