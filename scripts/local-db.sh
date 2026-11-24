#!/usr/bin/env bash
# Private throwaway cluster. Evaluate start's output; stop with the printed root in the environment.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python scripts/pgtap/local_db.py "$@"
