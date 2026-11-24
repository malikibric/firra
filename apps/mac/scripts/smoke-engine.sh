#!/usr/bin/env bash
# Synthetic data only; re-enter with an empty environment and short private socket path.
set -euo pipefail
if [[ "${1:-}" != --isolated ]]; then
  [[ $# == 1 ]] || { echo "Usage: $0 bundle-directory" >&2; exit 2; }
  BUNDLE="$(cd "$1" && pwd)"
  TEMP="$(mktemp -d /private/tmp/firra-engine.XXXXXX)"
  trap 'rm -rf "$TEMP"' EXIT
  mkdir "$TEMP/home"
  env -i HOME="$TEMP/home" PATH=/usr/bin:/bin /bin/bash "$0" --isolated "$BUNDLE" "$TEMP"
  exit
fi
BUNDLE="$2"
TEMP="$3"
PG="$BUNDLE/postgres/bin"
PYTHON="$BUNDLE/python/bin/python3.12"
DATA="$TEMP/pg"
mkdir -m 0700 "$DATA" "$TEMP/socket"
run() { /usr/bin/perl -e 'alarm 30; exec @ARGV or die "exec failed\n"' "$@"; }
cleanup() {
  if [[ -n "${PY_PID:-}" ]]; then kill "$PY_PID" 2>/dev/null || true; wait "$PY_PID" 2>/dev/null || true; fi
  if [[ -f "$DATA/postmaster.pid" ]]; then run "$PG/pg_ctl" -D "$DATA" -m fast -w -t 15 stop; fi
}
trap cleanup EXIT
# Native timing must not depend on Python successfully passing the launch assessment.
clock() { /usr/bin/perl -MTime::HiRes=time -e 'printf "%.6f", time'; }
elapsed() { awk -v start="$1" -v end="$2" 'BEGIN {printf "%.3f", end-start}'; }
START="$(clock)"
run "$PG/initdb" -D "$DATA" -U firra --auth=trust --encoding=UTF8 --locale=C > "$TEMP/initdb.log"
END="$(clock)"
echo "initdb_seconds=$(elapsed "$START" "$END")"
# Use a config file to preserve spaces without shell quoting in pg_ctl's -o argument.
cat >> "$DATA/postgresql.conf" <<CONFIG
listen_addresses = ''
unix_socket_directories = '$TEMP/socket'
unix_socket_permissions = 0700
shared_buffers = '32MB'
CONFIG
START="$(clock)"
run "$PG/pg_ctl" -D "$DATA" -l "$TEMP/postgres.log" -w -t 15 start
END="$(clock)"
echo "postgres_start_seconds=$(elapsed "$START" "$END")"
PID="$(head -1 "$DATA/postmaster.pid")"
# Observe all descendants, because Postgres children inherit sockets. Repeat while Python is idle.
check_tcp() {
  local process result status
  for process in "$PID" $(pgrep -P "$PID"); do
    status=0
    result="$(/usr/sbin/lsof -nP -a -p "$process" -iTCP -sTCP:LISTEN 2>&1)" || status=$?
    [[ $status == 0 || ( $status == 1 && -z "$result" ) ]] || { echo "$result" >&2; return 1; }
    [[ -z "$result" ]] || { echo "$result" >&2; return 1; }
  done
  echo 'lsof_tcp_listeners=(empty)'
}
check_tcp
run "$PG/psql" -X -v ON_ERROR_STOP=1 -h "$TEMP/socket" -U firra -d postgres <<'SQL'
CREATE EXTENSION vector;
CREATE TABLE engine_smoke (id integer PRIMARY KEY, embedding vector(768));
INSERT INTO engine_smoke VALUES (1, array_fill(1.0::real, ARRAY[768])::vector);
DO $$ BEGIN
  IF (SELECT embedding <-> array_fill(1.0::real, ARRAY[768])::vector FROM engine_smoke WHERE id=1) <> 0 THEN
    RAISE EXCEPTION 'vector round-trip failed';
  END IF;
END $$;
SELECT vector_dims(embedding) AS dimensions FROM engine_smoke;
SHOW listen_addresses;
SQL
START="$(clock)"
/usr/bin/perl -e 'alarm 30; exec @ARGV or die "exec failed\n"' \
  "$PYTHON" -I -B - "$TEMP/socket" "$TEMP/python-ready" <<'PY' &
import pathlib
import sys
import time
import firra
import firra_contracts
import psycopg
import fastapi
import cryptography
import uvloop
import httptools
with psycopg.connect(host=sys.argv[1], user="firra", dbname="postgres") as connection:
    assert connection.execute("select 1").fetchone() == (1,)
    assert connection.execute("show listen_addresses").fetchone() == ("",)
    assert connection.execute("select vector_dims(embedding) from engine_smoke").fetchone() == (768,)
pathlib.Path(sys.argv[2]).write_text(str(time.time()))
time.sleep(2)
print("python_select_1=ok runtime_imports=ok")
PY
PY_PID=$!
for ((attempt=0; attempt<100; attempt++)); do
  [[ -f "$TEMP/python-ready" ]] && break
  kill -0 "$PY_PID" 2>/dev/null || { wait "$PY_PID"; exit 1; }
  sleep 0.1
done
[[ -f "$TEMP/python-ready" ]]
echo "python_first_select_seconds=$(elapsed "$START" "$(cat "$TEMP/python-ready")")"
echo "postgres_rss_kib=$(ps -o rss= -p "$PID" | tr -d ' ')"
echo "postgres_children_rss_kib=$(ps -o rss= -p "$(pgrep -P "$PID" | paste -sd, -)" | awk '{sum+=$1} END {print sum}')"
echo "python_idle_rss_kib=$(ps -o rss= -p "$PY_PID" | tr -d ' ')"
check_tcp
wait "$PY_PID"
run "$PG/pg_ctl" -D "$DATA" -m fast -w -t 15 stop
[[ ! -f "$DATA/postmaster.pid" ]]
echo 'smoke_engine=ok'
