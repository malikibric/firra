# AGENTS.md — Firra

Instructions for coding agents (Claude Code, Codex, Cursor and others). **`CLAUDE.md` is the source of truth**: read it fully, then
`docs/plans/local-v1.md` and the doc for the area you touch (`docs/architecture.md`, `docs/connectors.md`, `docs/north-star.md`).
Product rules (§2) and guardrails (§6) are non-negotiable; if a request conflicts with them, stop and ask.
Documents of the earlier cloud-first design are not part of this tree and are **not** the rules.

## Start of every session

1. Read `CLAUDE.md`, `docs/plans/local-v1.md` and the doc for the area you touch. Read `CONTRIBUTING.md` for the workflow.
2. Work on a branch, never on `main`: `<type>/<short-slug>`. Commit often and open a pull request when `scripts/check.sh <area>` is green.
3. `git log --oneline -30` shows the working style in practice.

## Repository map

- `services/worker` — Python **engine** (package `firra`): connectors (Gmail, Calendar, Slack), clean, resolve, policy, extract
  (executors for the user's AI), verify, memory, signals, assessment, replay, routines, read models, MCP tools; local runtime in
  `firra/local/`. Every judgement is computed and stored here.
- `apps/mac` — native macOS app (Swift/SwiftUI), the only UI. It starts the engine and talks to it on 127.0.0.1.
- `apps/api` — TypeScript **control plane**: relay for MCP over a link, OAuth server for connectors, device registration, public
  pages. It never sees client data. (Its old read/setup/connect routes are deleted in phase F9.)
- `packages/contracts` — JSON Schemas / OpenAPI → generated zod + Pydantic. `packages/db-types`, `packages/config`.
- `supabase/migrations` — forward-only SQL, run unchanged on the local Postgres after `supabase/local/shim.sql`;
  `supabase/tests` — pgTAP. `infra/terraform` — the small cloud only.
- Engine and control plane talk only through the relay protocol (`packages/contracts`) and typed payloads.

## How work is done here

- **Order of work is the plan** (`docs/plans/local-v1.md`): phases and gates. Open an issue before starting anything large.
- **Ask before** new dependencies (approved: `mcp`, `ws`), anything that sends client data anywhere, policy logic, billing.
- **Small vertical commits**, Conventional Commits with a scope: `feat(worker): …`, `feat(mac): …`, `feat(api): …`, `feat(db): …`,
  `fix: …`, `docs: …`, `test: …`. Subject says what the user/system gains; body explains *why* when not obvious.
- **Tests with every change**, all external APIs and all AI calls faked (`services/worker/tests/fake_google.py`, fake CLI binaries
  for executors). Integration tests run on the local Postgres; pgTAP for every new table/function.
- **Provider integrations:** verify current provider docs first and record what you checked in the module docstring
  ("Docs checked: …").
- **Code style:** read the neighbouring code and match it — small modules, explicit names, comments only where the *why* is not
  obvious, no speculative abstractions, no dead code.
- **Logs:** ids, counts, timings, error classes only — never bodies, Slack text, quotes, email addresses, tokens
  (`services/worker/firra/logging.py` redacts free text).
- **Definition of done:** `scripts/check.sh <area>` green, docs (`docs/`, `.env.example`) updated
  when behaviour, config or schema change, branch pushed.
- **No spend, no remote state:** never call a paid API, `gcloud`, `terraform apply`, `supabase db push`/`link`, `gh workflow run`; never connect to the cloud project from the engine. Only synthetic data.
- **Language:** everything in the repo (code, comments, docs, commits) in English.

## Commands

```bash
scripts/check.sh worker        # ruff, format, mypy, pytest (also: contracts ts api sql mac security terraform all)
scripts/check.sh sql           # pgTAP + integration tests (bundled Postgres first, Docker/Supabase only as fallback)
pnpm codegen                   # after contract changes (commit the output)
apps/mac/scripts/test.sh       # Mac app tests
```

Keep clones outside iCloud-synced folders: git operations inside iCloud hang and iCloud creates `… 2` duplicate files. If you
find one, delete it and never commit it.

## Never

- Read, copy or log real customer data. Commit a credential (a stray `settings.json` once held one: it was removed and revoked).
- Send client data to any server of ours, or to any AI other than the owner's own signed-in tool.
- Build anything out of scope (`CLAUDE.md` §15): unbilled-money recovery, voice/video/emotion analysis, employee scoring,
  auto-sending to clients, LinkedIn scraping, team features.
