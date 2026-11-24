#!/usr/bin/env bash
# Local replacement for .github/workflows/ci.yml: GitHub Actions minutes are limited, so every check runs on the
# Mac. Usage: scripts/check.sh [area ...]   areas: contracts ts worker api sql mac security terraform all (default: all)
#   FIRRA_CHECK_SKIP="sql mac"  skips areas when running `all` (say so in the pull request).
# `sql` prefers bundled PostgreSQL; Docker/Supabase remains the fallback.
set -euo pipefail
cd "$(dirname "$0")/.."

PASSED=(); FAILED=(); SKIPPED=()
skip_list=" ${FIRRA_CHECK_SKIP:-} "

step() { # step <name> <command...>
  local name="$1"; shift
  printf '\n\033[1m== %s\033[0m\n' "$name"
  if "$@"; then PASSED+=("$name"); else FAILED+=("$name"); fi
}
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing tool: $1 ($2)"; return 1; }; }

check_contracts() {
  step "contracts codegen is current" bash -c 'pnpm codegen >/dev/null && git diff --exit-code -- packages'
}
check_ts() {
  step "pnpm install" pnpm install --frozen-lockfile
  step "prettier" pnpm format:check
  step "turbo lint typecheck test build" pnpm turbo run lint typecheck test build
}
check_worker() {
  step "uv sync" uv sync --all-packages --frozen
  step "ruff check" uv run ruff check .
  step "ruff format" uv run ruff format --check .
  step "mypy" uv run mypy services/worker/firra services/worker/tests services/relay packages/contracts/python
  step "pytest (unit)" uv run pytest services/worker services/relay packages/contracts/python
}
docker_up() { docker info >/dev/null 2>&1 || { echo "Docker is not running: open -a Docker, wait for it, retry (or FIRRA_CHECK_SKIP=sql)"; return 1; }; }
start_local_db() {
  local env_output
  env_output="$(scripts/local-db.sh start)" || return 1
  eval "$env_output"
  trap 'scripts/local-db.sh stop' EXIT
}
check_sql() {
  local pg_bin="${FIRRA_LOCAL_PG_BIN:-$HOME/firra-tools/engine/postgres/bin}"
  if [[ -x "$pg_bin/postgres" ]]; then
    export FIRRA_LOCAL_PG_BIN="$pg_bin"
    step "bundled database start" start_local_db
    [[ " ${FAILED[*]-} " == *"bundled database start"* ]] && return 0
    step "pgTAP" uv run python scripts/pgtap/run.py
    step "worker integration tests" uv run pytest services/worker -m integration --tb=short -ra
    return 0
  fi
  step "docker is running" docker_up
  [[ " ${FAILED[*]-} " == *"docker is running"* ]] && return 0
  step "supabase start" bash -c 'pnpm exec supabase start -x studio,vector,logflare,imgproxy'
  eval "$(pnpm exec supabase status -o env 2>/dev/null \
    | grep -E '^(API_URL|PUBLISHABLE_KEY|SECRET_KEY|DB_URL)=' \
    | sed -e 's/^API_URL=/export SUPABASE_URL=/' -e 's/^PUBLISHABLE_KEY=/export SUPABASE_PUBLISHABLE_KEY=/' \
          -e 's/^SECRET_KEY=/export SUPABASE_SECRET_KEY=/' -e 's/^DB_URL=/export FIRRA_DATABASE_URL=/')" || true
  step "pgTAP" pnpm exec supabase test db
  step "db-types are current" bash -c 'pnpm --filter @firra/db-types generate && git diff --exit-code -- packages/db-types'
  step "worker integration tests" bash -c 'cd services/worker && uv run pytest -m integration'
}
check_api() {
  step "api build deps" pnpm turbo run build --filter=@firra/api^...
  step "api integration tests (needs sql area up)" pnpm --filter @firra/api test:integration
}
check_mac() { step "mac tests" apps/mac/scripts/test.sh; step "mac build" bash -c 'cd apps/mac && swift build'; }
check_security() {
  step "gitleaks" bash -c 'need() { command -v gitleaks >/dev/null || { echo "install gitleaks: brew install gitleaks"; exit 1; }; }; need; gitleaks detect --no-banner --redact --log-opts="origin/main..HEAD"'
  step "semgrep" uvx semgrep scan --metrics off --error --config p/default --config p/typescript --config p/python --config p/dockerfile --config p/terraform
}
check_terraform() {
  step "terraform fmt" terraform -chdir=infra/terraform fmt -check -recursive
  step "terraform validate" bash -c 'for e in staging prod; do terraform -chdir=infra/terraform/envs/$e init -backend=false -input=false >/dev/null && terraform -chdir=infra/terraform/envs/$e validate || exit 1; done'
}

areas=("$@"); [[ ${#areas[@]} -eq 0 ]] && areas=(all)
expand() { for a in "${areas[@]}"; do [[ $a == all ]] && echo contracts ts worker sql api mac security terraform || echo "$a"; done; }
for a in $(expand); do
  if [[ "$skip_list" == *" $a "* ]]; then SKIPPED+=("$a"); continue; fi
  case "$a" in
    contracts) check_contracts ;; ts) check_ts ;; worker) check_worker ;; sql) check_sql ;; api) check_api ;;
    mac) check_mac ;; security) check_security ;; terraform) check_terraform ;;
    *) echo "unknown area: $a"; exit 2 ;;
  esac
done

printf '\n\033[1m== summary\033[0m\n'
((${#PASSED[@]})) && printf 'passed:  %s\n' "${PASSED[@]}"
((${#SKIPPED[@]})) && printf 'skipped: %s\n' "${SKIPPED[@]}"
((${#FAILED[@]})) && { printf 'FAILED:  %s\n' "${FAILED[@]}"; exit 1; }
echo "all selected checks passed"
