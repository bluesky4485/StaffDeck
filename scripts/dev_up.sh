#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
ENTERPRISE_DIR="$ROOT_DIR/frontend-enterprise"
CHAT_DIR="$ROOT_DIR/frontend-chat"
RUN_DIR="$ROOT_DIR/.dev"
LOG_DIR="$RUN_DIR/logs"

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
ENTERPRISE_HOST="${ENTERPRISE_HOST:-127.0.0.1}"
ENTERPRISE_PORT="${ENTERPRISE_PORT:-5173}"
CHAT_HOST="${CHAT_HOST:-127.0.0.1}"
CHAT_PORT="${CHAT_PORT:-5174}"
FORCE_PORTS="${FORCE_PORTS:-0}"
DETACH="${DETACH:-0}"
AUTO_RESTART="${AUTO_RESTART:-$DETACH}"

api_default_host="$BACKEND_HOST"
if [[ "$api_default_host" == "0.0.0.0" ]]; then
  api_default_host="127.0.0.1"
fi
API_BASE_URL="${VITE_API_BASE_URL:-${API_BASE_URL:-http://$api_default_host:$BACKEND_PORT}}"

DEFAULT_CORS_ORIGINS="http://localhost:$ENTERPRISE_PORT,http://localhost:$CHAT_PORT,http://127.0.0.1:$ENTERPRISE_PORT,http://127.0.0.1:$CHAT_PORT"
if [[ -n "${PUBLIC_ENTERPRISE_ORIGIN:-}" ]]; then
  DEFAULT_CORS_ORIGINS="$DEFAULT_CORS_ORIGINS,$PUBLIC_ENTERPRISE_ORIGIN"
fi
if [[ -n "${PUBLIC_CHAT_ORIGIN:-}" ]]; then
  DEFAULT_CORS_ORIGINS="$DEFAULT_CORS_ORIGINS,$PUBLIC_CHAT_ORIGIN"
fi
CORS_ORIGINS="${CORS_ORIGINS:-$DEFAULT_CORS_ORIGINS}"

mkdir -p "$RUN_DIR" "$LOG_DIR"

remove_legacy_launchctl_labels() {
  for prefix in com.ultrarag4.dev com.skill-agent-loop; do
    for name in backend enterprise chat; do
      launchctl remove "$prefix.$name" >/dev/null 2>&1 || true
    done
  done
}

stop_pid_file() {
  local name="$1"
  local pid_file="$RUN_DIR/$name.pid"
  [[ -f "$pid_file" ]] || return 0

  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  rm -f "$pid_file"

  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
  fi
}

port_pids() {
  local port="$1"
  lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

ensure_port_free() {
  local port="$1"
  local pids
  pids="$(port_pids "$port")"
  [[ -n "$pids" ]] || return 0

  if [[ "$FORCE_PORTS" == "1" ]]; then
    while read -r pid; do
      [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    done <<< "$pids"
    sleep 0.3
    return 0
  fi

  echo "Port $port is already in use by PID(s): $pids" >&2
  echo "Run scripts/dev_down.sh first, or use FORCE_PORTS=1 scripts/dev_up.sh to release unmanaged listeners." >&2
  exit 1
}

start_service() {
  local name="$1"
  local cwd="$2"
  local command="$3"
  local log_file="$LOG_DIR/$name.log"
  local err_file="$LOG_DIR/$name.err.log"
  local pid_file="$RUN_DIR/$name.pid"

  : > "$log_file"
  : > "$err_file"
  if [[ "$DETACH" == "1" ]]; then
    local pid
    pid="$(
      python3 -c '
import subprocess
import sys

cwd, command, log_file, err_file = sys.argv[1:5]
with open(log_file, "ab", buffering=0) as stdout, open(err_file, "ab", buffering=0) as stderr:
    process = subprocess.Popen(
        ["/bin/zsh", "-lc", command],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
print(process.pid)
' "$cwd" "$command" "$log_file" "$err_file"
    )"
  else
    /bin/zsh -lc "cd '$cwd' && $command" >"$log_file" 2>"$err_file" &
    local pid="$!"
  fi
  echo "$pid" > "$pid_file"
  echo "$pid"
}

url_host() {
  local host="$1"
  if [[ "$host" == "0.0.0.0" ]]; then
    echo "127.0.0.1"
  else
    echo "$host"
  fi
}

wait_url() {
  local label="$1"
  local url="$2"
  local log_file="$3"
  for _ in {1..80}; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  echo "$label failed to become ready: $url" >&2
  echo "Last log lines from $log_file:" >&2
  tail -n 80 "$log_file" >&2 || true
  exit 1
}

cleanup() {
  for name in supervisor backend enterprise chat; do
    stop_pid_file "$name"
  done
}

remove_legacy_launchctl_labels

for name in supervisor backend enterprise chat; do
  stop_pid_file "$name"
done

ensure_port_free "$BACKEND_PORT"
ensure_port_free "$ENTERPRISE_PORT"
ensure_port_free "$CHAT_PORT"

if [[ "$DETACH" == "1" && "$AUTO_RESTART" == "1" ]]; then
  supervisor_log="$LOG_DIR/supervisor.log"
  supervisor_err_file="$LOG_DIR/supervisor.err.log"
  : > "$supervisor_log"
  : > "$supervisor_err_file"
  for name in backend enterprise chat; do
    : > "$LOG_DIR/$name.log"
    : > "$LOG_DIR/$name.err.log"
  done
  supervisor_pid="$(
    python3 -c '
import os
import subprocess
import sys

root_dir, script_path, log_file, err_file = sys.argv[1:5]
env = os.environ.copy()
with open(log_file, "ab", buffering=0) as stdout, open(err_file, "ab", buffering=0) as stderr:
    process = subprocess.Popen(
        [sys.executable, script_path],
        cwd=root_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
print(process.pid)
' "$ROOT_DIR" "$ROOT_DIR/scripts/dev_supervisor.py" "$supervisor_log" "$supervisor_err_file"
  )"
  echo "$supervisor_pid" > "$RUN_DIR/supervisor.pid"

  backend_url_host="$(url_host "$BACKEND_HOST")"
  enterprise_url_host="$(url_host "$ENTERPRISE_HOST")"
  chat_url_host="$(url_host "$CHAT_HOST")"

  wait_url "backend" "http://$backend_url_host:$BACKEND_PORT/api/health" "$LOG_DIR/backend.log"
  wait_url "enterprise" "http://$enterprise_url_host:$ENTERPRISE_PORT/enterprise/dashboard" "$LOG_DIR/enterprise.log"
  wait_url "chat" "http://$chat_url_host:$CHAT_PORT/chat" "$LOG_DIR/chat.log"

  echo "Started stable supervisor:"
  echo "  supervisor $supervisor_pid"
  echo "  backend    http://$backend_url_host:$BACKEND_PORT/docs"
  echo "  enterprise http://$enterprise_url_host:$ENTERPRISE_PORT/enterprise/dashboard"
  echo "  chat       http://$chat_url_host:$CHAT_PORT/chat"
  echo
  echo "Frontend API base:"
  echo "  $API_BASE_URL"
  echo
  echo "Backend CORS origins:"
  echo "  $CORS_ORIGINS"
  echo
  echo "Logs:"
  echo "  $LOG_DIR/supervisor.log"
  echo "  $LOG_DIR/backend.log"
  echo "  $LOG_DIR/enterprise.log"
  echo "  $LOG_DIR/chat.log"
  echo
  echo "Detached with auto-restart. Use scripts/dev_down.sh to stop."
  exit 0
fi

backend_pid="$(start_service "backend" "$BACKEND_DIR" "export CORS_ORIGINS='$CORS_ORIGINS'; exec .venv/bin/uvicorn app.main:app --host '$BACKEND_HOST' --port '$BACKEND_PORT'")"
enterprise_pid="$(start_service "enterprise" "$ENTERPRISE_DIR" "export VITE_API_BASE_URL='$API_BASE_URL'; exec ./node_modules/.bin/vite --host '$ENTERPRISE_HOST' --port '$ENTERPRISE_PORT' --strictPort")"
chat_pid="$(start_service "chat" "$CHAT_DIR" "export VITE_API_BASE_URL='$API_BASE_URL'; exec ./node_modules/.bin/vite --host '$CHAT_HOST' --port '$CHAT_PORT' --strictPort")"

backend_url_host="$(url_host "$BACKEND_HOST")"
enterprise_url_host="$(url_host "$ENTERPRISE_HOST")"
chat_url_host="$(url_host "$CHAT_HOST")"

wait_url "backend" "http://$backend_url_host:$BACKEND_PORT/api/health" "$LOG_DIR/backend.log"
wait_url "enterprise" "http://$enterprise_url_host:$ENTERPRISE_PORT/enterprise/dashboard" "$LOG_DIR/enterprise.log"
wait_url "chat" "http://$chat_url_host:$CHAT_PORT/chat" "$LOG_DIR/chat.log"

echo "Started:"
echo "  backend    http://$backend_url_host:$BACKEND_PORT/docs ($backend_pid)"
echo "  enterprise http://$enterprise_url_host:$ENTERPRISE_PORT/enterprise/dashboard ($enterprise_pid)"
echo "  chat       http://$chat_url_host:$CHAT_PORT/chat ($chat_pid)"
echo
echo "Frontend API base:"
echo "  $API_BASE_URL"
echo
echo "Backend CORS origins:"
echo "  $CORS_ORIGINS"
echo
echo "Logs:"
echo "  $LOG_DIR/backend.log"
echo "  $LOG_DIR/enterprise.log"
echo "  $LOG_DIR/chat.log"

if [[ "$DETACH" == "1" ]]; then
  echo
  echo "Detached. Use scripts/dev_down.sh to stop."
  exit 0
fi

trap cleanup INT TERM EXIT
echo
echo "Supervisor running. Press Ctrl-C to stop all services."
while true; do
  for pid in "$backend_pid" "$enterprise_pid" "$chat_pid"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Service process $pid exited; stopping remaining services." >&2
      exit 1
    fi
  done
  sleep 1
done
