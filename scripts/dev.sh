#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT_DIR/web"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env"
  set +a
fi

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

YELLKA_WEB_HOST="${YELLKA_WEB_HOST:-127.0.0.1}"
YELLKA_WEB_TOKEN="${YELLKA_WEB_TOKEN:-dev-token}"
YELLKA_DB="${YELLKA_DB:-$ROOT_DIR/balance.sqlite3}"
API_PORT_START="${YELLKA_WEB_PORT:-8001}"
VITE_PORT_START="${VITE_PORT:-5173}"
VITE_HOST="${VITE_HOST:-127.0.0.1}"

find_free_port() {
  "$PYTHON_BIN" - "$1" "$2" <<'PY'
import socket
import sys

start = int(sys.argv[1])
host = sys.argv[2]

for port in range(start, start + 100):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            continue
        print(port)
        raise SystemExit(0)

raise SystemExit(f"No free port found from {start} to {start + 99}")
PY
}

YELLKA_WEB_PORT="$(find_free_port "$API_PORT_START" "$YELLKA_WEB_HOST")"
VITE_PORT="$(find_free_port "$VITE_PORT_START" "$VITE_HOST")"
API_PROXY_HOST="${YELLKA_WEB_PROXY_HOST:-$YELLKA_WEB_HOST}"
if [ "$API_PROXY_HOST" = "0.0.0.0" ]; then
  API_PROXY_HOST="127.0.0.1"
fi
VITE_API_PROXY_TARGET="http://$API_PROXY_HOST:$YELLKA_WEB_PORT"

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export YELLKA_WEB_HOST
export YELLKA_WEB_PORT
export YELLKA_WEB_TOKEN
export YELLKA_DB
export VITE_API_PROXY_TARGET
export VITE_YELLKA_WEB_TOKEN="$YELLKA_WEB_TOKEN"

api_pid=""
web_pid=""
discord_pid=""
cleanup_done=0

cleanup() {
  if [ "$cleanup_done" -eq 1 ]; then
    return
  fi
  cleanup_done=1
  stop_process_group "$web_pid"
  stop_process_group "$discord_pid"
  stop_process_group "$api_pid"
}

stop_process_group() {
  local pid="${1:-}"
  local attempt
  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    return
  fi
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  for attempt in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      return
    fi
    sleep 0.1
  done
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

echo "Starting Yellka Shop 3.0"
echo "API:      http://$YELLKA_WEB_HOST:$YELLKA_WEB_PORT"
echo "Frontend: http://$VITE_HOST:$VITE_PORT"
echo "DB:       $YELLKA_DB"
echo "Token:    $YELLKA_WEB_TOKEN"
if [ -n "${DISCORD_BOT_TOKEN:-}" ]; then
  echo "Discord:  command bot enabled"
else
  echo "Discord:  command bot disabled (DISCORD_BOT_TOKEN is empty)"
fi
echo

if [ ! -d "$WEB_DIR/node_modules" ]; then
  echo "Missing web/node_modules. Run: cd web && npm install" >&2
  exit 1
fi

setsid "$PYTHON_BIN" -m yellka.web_api &
api_pid="$!"

"$PYTHON_BIN" - "$YELLKA_WEB_HOST" "$YELLKA_WEB_PORT" <<'PY'
import sys
import time
import urllib.request

host = sys.argv[1]
port = sys.argv[2]
url = f"http://{host}:{port}/api/health"

for _ in range(60):
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            if response.status == 200:
                raise SystemExit(0)
    except Exception:
        time.sleep(0.25)

raise SystemExit("API did not become ready in time")
PY

cd "$WEB_DIR"
setsid npm run dev -- --host "$VITE_HOST" --port "$VITE_PORT" --strictPort &
web_pid="$!"

if [ -n "${DISCORD_BOT_TOKEN:-}" ]; then
  setsid "$PYTHON_BIN" -m yellka --db "$YELLKA_DB" discord &
  discord_pid="$!"
fi

wait -n "$api_pid" "$web_pid" ${discord_pid:+"$discord_pid"}
status="$?"
cleanup
exit "$status"
