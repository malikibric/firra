# Local development

> **Local v1:** the engine runs on the Mac against the bundled PostgreSQL (no Docker needed).
> `scripts/check.sh sql` starts a throwaway bundled cluster first and uses Docker/Supabase only as a
> fallback. The `supabase start`, Cloud Tasks and `api → worker` split below describe the legacy
> cloud-era flow and stay only until phase F9.

## Prerequisites

- No Docker needed for engine work: the bundled PostgreSQL (via `FIRRA_LOCAL_PG_BIN`,
  default `~/firra-tools/engine/postgres/bin`; see "Bundled database checks" below). Docker Desktop
  (or OrbStack) only for the Supabase fallback path of `scripts/check.sh sql`.
- Node 24 (`.nvmrc`), pnpm 10 (`corepack enable`)
- uv (installs Python 3.12 from `.python-version`)
- Terraform ≥ 1.9 and gcloud (infra work only)

The Supabase CLI is a pinned dev dependency: use `pnpm exec supabase …`.

## First-time setup

```bash
pnpm install
uv sync --all-packages
cp .env.example .env
eval "$(scripts/local-db.sh start)"   # bundled Postgres; prints FIRRA_DATABASE_URL for this shell
scripts/local-db.sh stop
openssl rand -hex 32              # use for both LOCAL_TASK_SECRET and FIRRA_LOCAL_TASK_SECRET
```

## Run

```bash
pnpm --filter @firra/worker dev   # http://127.0.0.1:8081 (FastAPI task endpoints)
pnpm --filter @firra/api dev      # http://127.0.0.1:8080 (Hono)
apps/mac/scripts/test.sh          # the Mac app's tests; build a DMG with apps/mac/scripts/package.sh
```

Locally, `TASK_QUEUE_MODE=local` makes the api call the worker directly with a short-lived HS256
token instead of Cloud Tasks. The worker refuses that mode outside `local`/`test`.

The worker's local dispatcher redelivers explicit HTTP 503 responses with exponential backoff
(1s, 2s, 4s, capped at 30s), bounded by the job's `max_attempts`. Exhausted jobs end `failed`.
Other HTTP errors are surfaced without retries. A transport/read timeout leaves the job unchanged
for the runner to observe: the worker may still be executing a paid call, so automatic resend
would risk duplicate execution. Background dispatch failures reach Python's thread exception hook;
direct dispatch callers receive `LocalDispatchError` (or its uncertain-delivery/failed-job subtype).
Local delivery uses its own HTTP client with a 570 s read timeout (the longest task time budget is
540 s), so the answer of a long task that ends in a retryable 503 is not lost to a shorter client
timeout; provider and metadata calls keep their short timeouts.

Magic-link emails land in Mailpit: http://127.0.0.1:54324.

## Tests

| What | Command |
|---|---|
| Everything TS + Python unit tests, lint, typecheck | `pnpm turbo run lint typecheck test` |
| Python only | `uv run ruff check . && uv run mypy services/worker/firra services/worker/tests packages/contracts/python && uv run pytest services/worker packages/contracts/python` |
| RLS / access rules (pgTAP) | `scripts/check.sh sql` (bundled Postgres; Docker/Supabase fallback) |
| Worker against local DB | `cd services/worker && uv run pytest -m integration` |
| api → worker → DB round-trip | `pnpm --filter @firra/api test:integration` |
| Mac app tests | `apps/mac/scripts/test.sh` |

## After changing things

- **Migration** (`supabase/migrations/*.sql`, forward-only): run `scripts/check.sh sql` (bundled
  Postgres applies `supabase/local/shim.sql` + migrations unchanged), add pgTAP tests,
  then `pnpm --filter @firra/db-types generate` and commit the regenerated types.
- **Contracts** (`packages/contracts/schemas/*.json`): `pnpm codegen` and commit the generated zod and
  Pydantic files. `scripts/check.sh contracts` fails on drift.

## Troubleshooting

- **`ModuleNotFoundError: firra_contracts` / `firra` although `uv sync` ran**: the repo is inside an
  iCloud-synced folder (e.g. Desktop). iCloud sets the macOS `hidden` flag on files in `.venv`, and
  Python skips hidden `.pth` files. Keep the venv out of sync:
  `mv .venv .venv.nosync && ln -s .venv.nosync .venv && chflags -R nohidden .venv.nosync`.
  Better: move the repository out of iCloud-synced folders (also keeps `.env` off iCloud).
- **Files or folders named `… 2` (e.g. `.venv 2`, `.git/index 2`)**: iCloud conflict copies. They
  are ignored by git and Prettier and can be deleted; moving the repo out of iCloud stops them.
- **Docker daemon crashes while building the web image**: raise Docker's memory limit or stop
  Supabase (`pnpm exec supabase stop`) during the build.
- **Auth emails rate-limited locally**: `supabase/config.toml` raises `auth.rate_limit.email_sent`
  for local runs; restart Supabase after editing the config.
# Local engine database

The PostgreSQL supervisor lives in `firra.local.pg.PostgresSupervisor`. Pass the bundled
`bin_dir`, a private `data_dir`, and an optional `run_dir`; `ensure_ready()` initializes the
cluster, starts socket-only PostgreSQL, creates `firra`, and applies the shim and migrations.
`stop()` performs fast shutdown. A different cluster major requires the explicit upgrade path.
The supervisor rotates a log of at least 5 MiB into `postgres.log.1` at the next start.

Use a copy of the S2 binaries for tests. Freshly re-signed quarantined binaries can stall in
macOS launch assessment; the accepted R1 workaround clears quarantine on a private copy.
Do not modify the build directory or disable Gatekeeper/SIP. The app implements this in L5-01.

```bash
mkdir -p ~/firra-tools
cp -R ~/firra-checkout/apps/mac/build-engine/Relocated\ Engine ~/firra-tools/engine-codex
xattr -cr ~/firra-tools/engine-codex
export FIRRA_LOCAL_PG_BIN=~/firra-tools/engine-codex/postgres/bin
uv run pytest services/worker/tests/integration/test_local_pg.py -m integration -q -s
```

Tests create and delete their own synthetic clusters, never a remote database. Data, run and
log directories and sockets are 0700. Only `local all postgres trust` is permitted; no TCP
listeners are created. Long socket paths fall back to a checked user-owned `/tmp/firra-<uid>`
directory. PostgreSQL statement/error-statement logging is disabled to keep SQL text out of logs.

## Bundled database checks (no Docker)

`scripts/check.sh sql` prefers `FIRRA_LOCAL_PG_BIN`, defaulting to
`~/firra-tools/engine/postgres/bin`, and falls back to the existing Docker/Supabase checks
when that bundle is absent. It copies the bundle into a private `/tmp/firra-test-db-*`
root, starts a socket-only throwaway cluster, applies the unchanged shim/migrations,
runs every pgTAP file and the worker integration tests, then stops and removes the copy
on exit. The supplied bundle should already be a quarantine-cleared copy as described
above; shared/build binaries are never changed. API integration tests still use Supabase
REST/Auth and run separately with `scripts/check.sh api`. Database type generation stays
on the Supabase fallback path because its CLI needs Docker; it is not an engine SQL gate.

For a manually managed test session:

```bash
eval "$(scripts/local-db.sh start)"
uv run python scripts/pgtap/run.py
uv run pytest services/worker -m integration --tb=short -ra
scripts/local-db.sh stop
```

Keep the printed `FIRRA_LOCAL_DB_ROOT`/`FIRRA_LOCAL_PG_BIN`/`FIRRA_DATABASE_URL` exports
in the same shell until `stop`. Without an existing `FIRRA_DATABASE_URL`, setting
`FIRRA_LOCAL_PG_BIN` also lets the integration session fixture start and stop its own
private cluster. The fixture does not install pgTAP because Python engine tests do not
need it.

pgTAP is dev-only, pure SQL from release **1.3.4**, downloaded over HTTPS with a pinned
SHA-256. `scripts/pgtap/build.py` performs the release Makefile's PostgreSQL 17 substitutions
without make/pg_config or a compiled extension, and installs SQL/control files only into
the disposable bundle copy. The runner rejects SQL failures, `not ok`, missing or mismatched
plans, broken assertion numbering and bail-outs. The repository's transactional test files
and relative helper includes run unchanged with `psql -X -qAt -v ON_ERROR_STOP=1`.
