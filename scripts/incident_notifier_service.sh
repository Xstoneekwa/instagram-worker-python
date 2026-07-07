#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${INCIDENT_NOTIFIER_ENV_FILE:-$ROOT_DIR/.env}"
LOG_DIR="${INCIDENT_NOTIFIER_LOG_DIR:-$ROOT_DIR/logs/incident-notifier}"
RUN_DIR="${INCIDENT_NOTIFIER_RUN_DIR:-$ROOT_DIR/.local/incident-notifier}"
LOCK_DIR="$RUN_DIR/notifier.lock"
PID_FILE="$RUN_DIR/notifier.pid"
STATE_FILE="$RUN_DIR/last_cycle.json"
PLIST_SOURCE="$ROOT_DIR/ops/launchd/com.boost.phonefarm.incident-notifier.plist"
PLIST_TARGET="$HOME/Library/LaunchAgents/com.boost.phonefarm.incident-notifier.plist"
LAUNCHD_LABEL="com.boost.phonefarm.incident-notifier"
SERVICE="$ROOT_DIR/incident_notification_service.py"
INTERVAL_SECONDS="${INCIDENT_NOTIFIER_INTERVAL_SECONDS:-60}"

if [[ -f "$ENV_FILE" ]]; then
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

_resolve_python_path() {
  if [[ -n "${INCIDENT_NOTIFIER_PYTHON:-}" && -x "${INCIDENT_NOTIFIER_PYTHON}" ]]; then
    printf '%s' "${INCIDENT_NOTIFIER_PYTHON}"
    return 0
  fi
  if [[ -x "$ROOT_DIR/.venv/bin/python3" ]]; then
    printf '%s' "$ROOT_DIR/.venv/bin/python3"
    return 0
  fi
  command -v python3
}

PYTHON_BIN="$(_resolve_python_path)"
mkdir -p "$LOG_DIR" "$RUN_DIR"

_pid_alive() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

_pid_is_notifier() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  local command_line
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"incident_notification_service.py"* && "$command_line" == *"--serve"* ]]
}

_existing_notifier_pid() {
  if [[ -f "$PID_FILE" ]]; then
    local recorded
    recorded="$(tr -dc '0-9' < "$PID_FILE" || true)"
    if _pid_alive "$recorded" && _pid_is_notifier "$recorded"; then
      printf '%s' "$recorded"
      return 0
    fi
  fi
  local pid
  while read -r pid; do
    if _pid_is_notifier "$pid"; then
      printf '%s' "$pid"
      return 0
    fi
  done < <(pgrep -f "incident_notification_service\\.py.*--serve" 2>/dev/null || true)
  return 1
}

_notifier_process_count() {
  local count=0
  local pid
  while read -r pid; do
    if _pid_is_notifier "$pid"; then
      count=$((count + 1))
    fi
  done < <(pgrep -f "incident_notification_service\\.py.*--serve" 2>/dev/null || true)
  printf '%s' "$count"
}

_clear_pid_and_lock() {
  rm -f "$PID_FILE"
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

_launchd_loaded() {
  launchctl print "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1
}

_require_start_preconditions() {
  local missing=0
  if [[ ! -f "$SERVICE" ]]; then
    echo "FAIL missing incident_notification_service.py in $ROOT_DIR" >&2
    missing=1
  fi
  if [[ -z "${SUPABASE_URL:-}" ]]; then
    echo "FAIL missing SUPABASE_URL in notifier env" >&2
    missing=1
  fi
  if [[ -z "${SUPABASE_SERVICE_ROLE_KEY:-}" ]]; then
    echo "FAIL missing SUPABASE_SERVICE_ROLE_KEY in notifier env" >&2
    missing=1
  fi
  if [[ "$missing" != "0" ]]; then
    return 2
  fi
}

_status_json() {
  local existing_pid=""
  local process_count launchd_loaded="false"
  if existing_pid="$(_existing_notifier_pid)"; then :; else existing_pid=""; fi
  process_count="$(_notifier_process_count)"
  if _launchd_loaded; then launchd_loaded="true"; fi
  INCIDENT_NOTIFIER_STATUS_PID="$existing_pid" \
  INCIDENT_NOTIFIER_STATUS_PROCESS_COUNT="$process_count" \
  INCIDENT_NOTIFIER_STATUS_LAUNCHD_LOADED="$launchd_loaded" \
  INCIDENT_NOTIFIER_STATUS_STATE_FILE="$STATE_FILE" \
  INCIDENT_NOTIFIER_STATUS_LOG_DIR="$LOG_DIR" \
  INCIDENT_NOTIFIER_STATUS_INTERVAL_SECONDS="$INTERVAL_SECONDS" \
  "$PYTHON_BIN" - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def as_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def as_int(value, default=0):
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


state = {}
state_file = os.environ.get("INCIDENT_NOTIFIER_STATUS_STATE_FILE", "")
try:
    raw = json.loads(Path(state_file).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        state = raw
except Exception:
    state = {}

pid = os.environ.get("INCIDENT_NOTIFIER_STATUS_PID", "").strip()
process_count = as_int(os.environ.get("INCIDENT_NOTIFIER_STATUS_PROCESS_COUNT", "0"))
process_running = bool(pid)
launchd_loaded = as_bool(os.environ.get("INCIDENT_NOTIFIER_STATUS_LAUNCHD_LOADED", "false"))
last_cycle_ok = state.get("ok") is True

if process_running and process_count > 1:
    status = "unhealthy"
elif process_running and last_cycle_ok:
    status = "running"
elif process_running:
    status = "degraded"
elif launchd_loaded:
    status = "starting"
else:
    status = "stopped"

payload = {
    "ok": status in {"running", "stopped", "starting", "degraded"},
    "status": status,
    "service_id": "incident-notifier",
    "processRunning": process_running,
    "pid": as_int(pid, 0) if pid else None,
    "processCount": process_count,
    "duplicateProcess": process_count > 1,
    "launchdLoaded": launchd_loaded,
    "intervalSeconds": as_int(os.environ.get("INCIDENT_NOTIFIER_STATUS_INTERVAL_SECONDS", "60"), 60),
    "lastCycleAt": state.get("at"),
    "lastCycleOk": last_cycle_ok,
    "lastSentCount": as_int(str(state.get("sent_count", 0))),
    "lastFailedCount": as_int(str(state.get("failed_count", 0))),
    "lastReason": state.get("reason"),
    "enabledChannels": state.get("enabled_channels"),
    "dryRun": state.get("dry_run"),
    "logsPath": os.path.join(os.environ.get("INCIDENT_NOTIFIER_STATUS_LOG_DIR", ""), "notifier.log"),
    "checkedAt": datetime.now(timezone.utc).isoformat(),
}
print(json.dumps(payload, sort_keys=True))
PY
}

_start_foreground() {
  _require_start_preconditions
  if existing_pid="$(_existing_notifier_pid)"; then
    if [[ -n "$existing_pid" && "$existing_pid" != "$$" ]]; then
      echo "incident_notifier_already_running pid=$existing_pid"
      exit 0
    fi
  fi
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    if existing_pid="$(_existing_notifier_pid)" && [[ -n "$existing_pid" && "$existing_pid" != "$$" ]]; then
      echo "incident_notifier_lock_held path=$LOCK_DIR"
      exit 0
    fi
    rmdir "$LOCK_DIR" 2>/dev/null || true
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
      echo "incident_notifier_lock_held path=$LOCK_DIR"
      exit 0
    fi
  fi
  trap _clear_pid_and_lock EXIT INT TERM
  "$PYTHON_BIN" "$SERVICE" \
    --env-file "$ENV_FILE" \
    --serve \
    --interval-seconds "$INTERVAL_SECONDS" \
    --state-file "$STATE_FILE" \
    >> "$LOG_DIR/notifier.log" 2>&1 &
  notifier_pid="$!"
  printf '%s\n' "$notifier_pid" > "$PID_FILE"
  wait "$notifier_pid"
}

cmd="${1:-start}"
shift || true
json_mode=false
for arg in "$@"; do
  case "$arg" in
    --json) json_mode=true ;;
    *) echo "Unknown option for $cmd: $arg" >&2; exit 2 ;;
  esac
done

case "$cmd" in
  start)
    _start_foreground
    ;;
  once)
    _require_start_preconditions
    "$PYTHON_BIN" "$SERVICE" --env-file "$ENV_FILE" --state-file "$STATE_FILE"
    ;;
  status)
    if [[ "$json_mode" == "true" ]]; then
      _status_json
      exit 0
    fi
    echo "service_id=incident-notifier"
    echo "root_dir=${ROOT_DIR}"
    if existing_pid="$(_existing_notifier_pid)"; then
      echo "process=running pid=${existing_pid}"
    else
      echo "process=stopped"
    fi
    ;;
  stop)
    launchctl stop "$LAUNCHD_LABEL" 2>/dev/null || true
    while read -r pid; do
      if _pid_is_notifier "$pid"; then
        kill "$pid" 2>/dev/null || true
      fi
    done < <(pgrep -f "incident_notification_service\\.py.*--serve" 2>/dev/null || true)
    _clear_pid_and_lock
    echo "incident_notifier_stop_requested"
    ;;
  restart)
    "$0" stop
    sleep 1
    if [[ ! -f "$PLIST_TARGET" ]]; then
      "$0" install
    else
      launchctl bootstrap "gui/$(id -u)" "$PLIST_TARGET" 2>/dev/null || true
      launchctl enable "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
    fi
    launchctl kickstart -k "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
    echo "incident_notifier_restart_requested label=$LAUNCHD_LABEL"
    ;;
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
    cp "$PLIST_SOURCE" "$PLIST_TARGET"
    chmod 644 "$PLIST_TARGET"
    launchctl bootout "gui/$(id -u)" "$PLIST_TARGET" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_TARGET"
    launchctl enable "gui/$(id -u)/$LAUNCHD_LABEL"
    echo "installed=$PLIST_TARGET"
    ;;
  launchd)
    launchctl print "gui/$(id -u)/$LAUNCHD_LABEL"
    ;;
  logs)
    echo "notifier_log=$LOG_DIR/notifier.log"
    echo "launchd_stdout=$LOG_DIR/launchd.stdout.log"
    echo "launchd_stderr=$LOG_DIR/launchd.stderr.log"
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    echo "Usage: scripts/incident_notifier_service.sh {start|once|status|stop|restart|install|launchd|logs}" >&2
    exit 2
    ;;
esac
