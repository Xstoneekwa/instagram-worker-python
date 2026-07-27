#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${DEVICE_HEARTBEAT_ENV_FILE:-$ROOT_DIR/.env}"
LOG_DIR="${DEVICE_HEARTBEAT_LOG_DIR:-$ROOT_DIR/logs/device-heartbeat-service}"
RUN_DIR="${DEVICE_HEARTBEAT_RUN_DIR:-$ROOT_DIR/.local/device-heartbeat-service}"
LOCK_DIR="$RUN_DIR/heartbeat.lock"
PID_FILE="$RUN_DIR/heartbeat.pid"
PAUSE_FILE="$RUN_DIR/paused"
STATE_FILE="$RUN_DIR/last_cycle.json"
PLIST_SOURCE="$ROOT_DIR/ops/launchd/com.boost.phonefarm.device-heartbeat.plist"
PLIST_TARGET="$HOME/Library/LaunchAgents/com.boost.phonefarm.device-heartbeat.plist"
LAUNCHD_LABEL="com.boost.phonefarm.device-heartbeat"
PUBLISHER="$ROOT_DIR/device_heartbeat_publisher.py"
INTERVAL_SECONDS="${DEVICE_HEARTBEAT_INTERVAL_SECONDS:-60}"
LOG_MAX_BYTES="${DEVICE_HEARTBEAT_LOG_MAX_BYTES:-10485760}"

if [[ -f "$ENV_FILE" ]]; then
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

_resolve_adb_path() {
  if [[ -n "${ADB_PATH:-}" && -x "${ADB_PATH}" ]]; then
    printf '%s' "${ADB_PATH}"
    return 0
  fi
  local candidate
  for candidate in \
    "${ANDROID_HOME:+$ANDROID_HOME/platform-tools/adb}" \
    "${ANDROID_SDK_ROOT:+$ANDROID_SDK_ROOT/platform-tools/adb}" \
    "${HOME}/Library/Android/sdk/platform-tools/adb" \
    "/opt/homebrew/bin/adb" \
    "/usr/local/bin/adb"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

if resolved_adb="$(_resolve_adb_path)"; then
  export ADB_PATH="$resolved_adb"
  export PATH="$(dirname "$resolved_adb"):${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"
fi

_resolve_python_path() {
  if [[ -n "${DEVICE_HEARTBEAT_PYTHON:-}" && -x "${DEVICE_HEARTBEAT_PYTHON}" ]]; then
    printf '%s' "${DEVICE_HEARTBEAT_PYTHON}"
    return 0
  fi
  if [[ -x "$ROOT_DIR/.venv/bin/python3" ]]; then
    printf '%s' "$ROOT_DIR/.venv/bin/python3"
    return 0
  fi
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    printf '%s' "$ROOT_DIR/.venv/bin/python"
    return 0
  fi
  command -v python3
}

PYTHON_BIN="$(_resolve_python_path)"

_resolve_host_label() {
  if [[ -n "${RUN_CONTROL_DISPATCHER_HOST_MACHINE:-}" ]]; then
    printf '%s' "$RUN_CONTROL_DISPATCHER_HOST_MACHINE"
    return 0
  fi
  local local_host_name=""
  if command -v scutil >/dev/null 2>&1; then
    local_host_name="$(scutil --get LocalHostName 2>/dev/null || true)"
  fi
  if [[ -n "$local_host_name" ]]; then
    printf '%s.local' "$local_host_name"
    return 0
  fi
  hostname 2>/dev/null || hostname -s
}

HOST_LABEL="$(_resolve_host_label)"
mkdir -p "$LOG_DIR" "$RUN_DIR"

_pid_alive() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

_pid_command_line() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  ps -p "$pid" -o command= 2>/dev/null || true
}

_pid_is_publisher() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  local command_line
  command_line="$(_pid_command_line "$pid")"
  [[ "$command_line" == *"device_heartbeat_publisher.py"* && "$command_line" == *"--serve"* ]]
}

_pid_is_wrapper() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  local command_line
  command_line="$(_pid_command_line "$pid")"
  [[ "$command_line" == *"device_heartbeat_service.sh start"* ]]
}

_record_pid() {
  local pid="${1:-$$}"
  printf '%s\n' "$pid" > "$PID_FILE"
}

_kill_pid_gracefully() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 0
  kill "$pid" 2>/dev/null || true
}

_kill_pid_force() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 0
  kill -9 "$pid" 2>/dev/null || true
}

_kill_all_publisher_processes() {
  local pid
  while read -r pid; do
    if _pid_is_publisher "$pid"; then
      _kill_pid_gracefully "$pid"
    fi
  done < <(pgrep -f "device_heartbeat_publisher\\.py.*--serve" 2>/dev/null || true)
  sleep 1
  while read -r pid; do
    if _pid_is_publisher "$pid" && _pid_alive "$pid"; then
      _kill_pid_force "$pid"
    fi
  done < <(pgrep -f "device_heartbeat_publisher\\.py.*--serve" 2>/dev/null || true)
  while read -r pid; do
    if _pid_is_wrapper "$pid"; then
      _kill_pid_gracefully "$pid"
    fi
  done < <(pgrep -f "device_heartbeat_service\\.sh start" 2>/dev/null || true)
}

_clear_pid_and_lock() {
  rm -f "$PID_FILE"
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

_existing_publisher_pid() {
  if [[ -f "$PID_FILE" ]]; then
    local recorded
    recorded="$(tr -dc '0-9' < "$PID_FILE" || true)"
    if _pid_alive "$recorded" && _pid_is_publisher "$recorded"; then
      printf '%s' "$recorded"
      return 0
    fi
  fi
  local pid
  while read -r pid; do
    if _pid_is_publisher "$pid"; then
      printf '%s' "$pid"
      return 0
    fi
  done < <(pgrep -f "device_heartbeat_publisher\\.py.*--serve" 2>/dev/null || true)
}

_publisher_process_count() {
  local count=0
  local pid
  while read -r pid; do
    if _pid_is_publisher "$pid"; then
      count=$((count + 1))
    fi
  done < <(pgrep -f "device_heartbeat_publisher\\.py.*--serve" 2>/dev/null || true)
  printf '%s' "$count"
}

_list_publisher_pids() {
  local pid
  while read -r pid; do
    if _pid_is_publisher "$pid"; then
      printf '%s\n' "$pid"
    fi
  done < <(pgrep -f "device_heartbeat_publisher\\.py.*--serve" 2>/dev/null || true)
}

_launchd_loaded() {
  launchctl print "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1
}

_read_state_json() {
  if [[ ! -f "$STATE_FILE" ]]; then
    printf '{}'
    return 0
  fi
  "$PYTHON_BIN" - <<'PY' "$STATE_FILE"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        payload = {}
except Exception:
    payload = {}
print(json.dumps(payload, sort_keys=True))
PY
}

_status_json() {
  local paused="false"
  local existing_pid=""
  local process_count="0"
  local launchd_loaded="false"
  local state_json
  state_json="$(_read_state_json)"

  if [[ -f "$PAUSE_FILE" ]]; then
    paused="true"
  fi
  if existing_pid="$(_existing_publisher_pid)" && [[ -n "$existing_pid" ]]; then
    :
  else
    existing_pid=""
  fi
  process_count="$(_publisher_process_count)"
  if _launchd_loaded; then
    launchd_loaded="true"
  fi

  DEVICE_HEARTBEAT_STATUS_PAUSED="$paused" \
  DEVICE_HEARTBEAT_STATUS_PID="$existing_pid" \
  DEVICE_HEARTBEAT_STATUS_PROCESS_COUNT="$process_count" \
  DEVICE_HEARTBEAT_STATUS_LAUNCHD_LOADED="$launchd_loaded" \
  DEVICE_HEARTBEAT_STATUS_STATE_JSON="$state_json" \
  DEVICE_HEARTBEAT_STATUS_LOG_DIR="$LOG_DIR" \
  DEVICE_HEARTBEAT_STATUS_PID_FILE="$PID_FILE" \
  DEVICE_HEARTBEAT_STATUS_STATE_FILE="$STATE_FILE" \
  DEVICE_HEARTBEAT_STATUS_INTERVAL_SECONDS="$INTERVAL_SECONDS" \
  "$PYTHON_BIN" - <<'PY'
import json
import os
from datetime import datetime, timezone


def as_bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def as_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


def safe_state(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


paused = as_bool(os.environ.get("DEVICE_HEARTBEAT_STATUS_PAUSED", "false"))
pid = os.environ.get("DEVICE_HEARTBEAT_STATUS_PID", "").strip()
process_count = as_int(os.environ.get("DEVICE_HEARTBEAT_STATUS_PROCESS_COUNT", "0"))
process_running = bool(pid)
launchd_loaded = as_bool(os.environ.get("DEVICE_HEARTBEAT_STATUS_LAUNCHD_LOADED", "false"))
state = safe_state(os.environ.get("DEVICE_HEARTBEAT_STATUS_STATE_JSON", "{}"))
last_cycle_at = str(state.get("at") or "")
last_cycle_ok = state.get("ok") is True
last_error = str(state.get("reason") or "") if state.get("ok") is False else ""
published_count = as_int(str(state.get("published_count", 0)))
observed_count = as_int(str(state.get("observed_count", 0)))
physical_phones_seen = as_int(str(state.get("physical_phones_seen", 0)))

if paused:
    status = "paused"
elif process_running and process_count > 1:
    status = "unhealthy"
elif process_running and last_cycle_ok:
    status = "running"
elif process_running and observed_count == 0:
    status = "degraded"
elif process_running:
    status = "degraded"
elif launchd_loaded:
    status = "starting"
else:
    status = "stopped"

if process_running and observed_count == 0 and last_cycle_ok:
    status = "degraded"

if not last_error and process_count > 1:
    last_error = "duplicate_publisher_processes"

payload = {
    "ok": status in {"running", "paused", "stopped", "starting", "degraded"},
    "status": status,
    "service_id": "device-heartbeat-publisher",
    "paused": paused,
    "processRunning": process_running,
    "pid": as_int(pid, 0) if pid else None,
    "processCount": process_count,
    "publisherPids": [as_int(part) for part in os.environ.get("DEVICE_HEARTBEAT_STATUS_PID", "").split(",") if part.strip().isdigit()],
    "duplicateProcess": process_count > 1,
    "launchdLoaded": launchd_loaded,
    "intervalSeconds": as_int(os.environ.get("DEVICE_HEARTBEAT_STATUS_INTERVAL_SECONDS", "60"), 60),
    "lastCycleAt": last_cycle_at or None,
    "lastCycleOk": last_cycle_ok,
    "lastPublishedCount": published_count,
    "lastObservedCount": observed_count,
    "physicalPhonesSeen": physical_phones_seen,
    "youngestHeartbeatAgeSeconds": None,
    "lastError": last_error or None,
    "logsPath": os.path.join(os.environ.get("DEVICE_HEARTBEAT_STATUS_LOG_DIR", ""), "heartbeat.log"),
    "pidFile": os.environ.get("DEVICE_HEARTBEAT_STATUS_PID_FILE", ""),
    "stateFile": os.environ.get("DEVICE_HEARTBEAT_STATUS_STATE_FILE", ""),
    "checkedAt": datetime.now(timezone.utc).isoformat(),
    "message": {
        "running": "Device heartbeat service is operational.",
        "paused": "Device heartbeat service is paused.",
        "degraded": "Device heartbeat service is running but needs attention.",
        "stopped": "Device heartbeat service is stopped.",
        "starting": "Device heartbeat service is starting or waiting for launchd.",
        "unhealthy": "Multiple heartbeat publishers detected.",
    }.get(status, "Device heartbeat service status unavailable."),
}
print(json.dumps(payload, sort_keys=True))
PY
}

_require_start_preconditions() {
  local missing=0
  if [[ ! -f "$PUBLISHER" ]]; then
    echo "FAIL missing device_heartbeat_publisher.py in $ROOT_DIR" >&2
    missing=1
  fi
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "FAIL python not executable: $PYTHON_BIN" >&2
    missing=1
  fi
  if [[ -z "${SUPABASE_URL:-}" ]]; then
    echo "FAIL missing SUPABASE_URL in heartbeat env" >&2
    missing=1
  fi
  if [[ -z "${SUPABASE_SERVICE_ROLE_KEY:-}" ]]; then
    echo "FAIL missing SUPABASE_SERVICE_ROLE_KEY in heartbeat env" >&2
    missing=1
  fi
  if [[ "$missing" != "0" ]]; then
    return 2
  fi
}

_start_foreground() {
  _require_start_preconditions
  if existing_pid="$(_existing_publisher_pid)"; then
    if [[ -n "$existing_pid" && "$existing_pid" != "$$" ]]; then
      echo "heartbeat_publisher_already_running pid=$existing_pid"
      exit 0
    fi
  fi
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    if existing_pid="$(_existing_publisher_pid)" && [[ -n "$existing_pid" && "$existing_pid" != "$$" ]]; then
      echo "heartbeat_lock_held path=$LOCK_DIR"
      exit 0
    fi
    rmdir "$LOCK_DIR" 2>/dev/null || true
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
      echo "heartbeat_lock_held path=$LOCK_DIR"
      exit 0
    fi
  fi
  trap _clear_pid_and_lock EXIT INT TERM
  _record_pid "$$"
  "$PYTHON_BIN" "$PUBLISHER" \
    --env-file "$ENV_FILE" \
    --adb "${ADB_PATH:-adb}" \
    --host-label "$HOST_LABEL" \
    --include-battery \
    --serve \
    --interval-seconds "$INTERVAL_SECONDS" \
    --state-file "$STATE_FILE" \
    --log-file "$LOG_DIR/heartbeat.log" \
    --log-max-bytes "$LOG_MAX_BYTES" &
  publisher_pid="$!"
  _record_pid "$publisher_pid"
  wait "$publisher_pid"
}

usage() {
  cat <<'EOF'
Usage: scripts/device_heartbeat_service.sh <command>

Commands:
  start         Run heartbeat publisher forever (blocks if already running)
  once          Run one heartbeat publish cycle
  status        Print service status
  stop          Stop the LaunchAgent or matching publisher process
  pause         Unload LaunchAgent and stop process
  resume        Reload LaunchAgent after pause
  restart       Stop then kickstart the LaunchAgent
  fix-duplicate Kill extra publisher processes and restart cleanly
  install       Install/load the user LaunchAgent
  launchd       Print launchd status for the heartbeat service
  logs          Print heartbeat log paths
EOF
}

cmd="${1:-start}"
shift || true
json_mode=false
path_mode=false
for arg in "$@"; do
  case "$arg" in
    --json) json_mode=true ;;
    --path) path_mode=true ;;
    *)
      echo "Unknown option for $cmd: $arg" >&2
      exit 2
      ;;
  esac
done

case "$cmd" in
  start)
    _start_foreground
    ;;
  once)
    _require_start_preconditions
    "$PYTHON_BIN" "$PUBLISHER" \
      --env-file "$ENV_FILE" \
      --adb "${ADB_PATH:-adb}" \
      --host-label "$HOST_LABEL" \
      --include-battery \
      --state-file "$STATE_FILE"
    ;;
  status)
    if [[ "$json_mode" == "true" ]]; then
      _status_json
      exit 0
    fi
    echo "service_id=device-heartbeat-publisher"
    echo "interval_seconds=${INTERVAL_SECONDS}"
    echo "root_dir=${ROOT_DIR}"
    echo "python=${PYTHON_BIN}"
    echo "adb_path=${ADB_PATH:-missing}"
    if existing_pid="$(_existing_publisher_pid)" && [[ -n "$existing_pid" ]]; then
      echo "process=running pid=${existing_pid}"
    else
      echo "process=stopped"
    fi
    if [[ -f "$PAUSE_FILE" ]]; then
      echo "paused=true"
    else
      echo "paused=false"
    fi
    ;;
  stop)
    launchctl stop "$LAUNCHD_LABEL" 2>/dev/null || true
    _kill_all_publisher_processes
    echo "heartbeat_stop_requested"
    rm -f "$PID_FILE"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    ;;
  pause)
    touch "$PAUSE_FILE"
    launchctl bootout "gui/$(id -u)" "$PLIST_TARGET" >/dev/null 2>&1 || true
    _kill_all_publisher_processes
    echo "heartbeat_paused"
    rm -f "$PID_FILE"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    ;;
  resume)
    rm -f "$PAUSE_FILE"
    process_count="$(_publisher_process_count)"
    if [[ "$process_count" == "1" ]] && _launchd_loaded; then
      echo "heartbeat_already_healthy publisher_count=1"
      exit 0
    fi
    if [[ "$process_count" -gt "1" ]]; then
      _kill_all_publisher_processes
      sleep 1
      process_count="$(_publisher_process_count)"
    fi
    if [[ ! -f "$PLIST_TARGET" ]]; then
      "$0" install
    else
      launchctl bootstrap "gui/$(id -u)" "$PLIST_TARGET" 2>/dev/null || true
      launchctl enable "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
    fi
    launchctl kickstart "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null \
      || launchctl kickstart -k "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null \
      || true
    echo "heartbeat_resumed label=$LAUNCHD_LABEL"
    ;;
  restart)
    rm -f "$PAUSE_FILE"
    _kill_all_publisher_processes
    sleep 1
    if [[ ! -f "$PLIST_TARGET" ]]; then
      "$0" install
    else
      launchctl bootstrap "gui/$(id -u)" "$PLIST_TARGET" 2>/dev/null || true
      launchctl enable "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
    fi
    launchctl kickstart -k "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
    echo "heartbeat_restart_requested label=$LAUNCHD_LABEL"
    ;;
  fix-duplicate)
    rm -f "$PAUSE_FILE"
    _kill_all_publisher_processes
    sleep 1
    rm -f "$PID_FILE"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    if [[ ! -f "$PLIST_TARGET" ]]; then
      "$0" install
    else
      launchctl bootstrap "gui/$(id -u)" "$PLIST_TARGET" 2>/dev/null || true
      launchctl enable "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
      launchctl kickstart -k "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
    fi
    echo "heartbeat_duplicate_fixed label=$LAUNCHD_LABEL"
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
    if [[ "$path_mode" == "true" ]]; then
      echo "$LOG_DIR/heartbeat.log"
    else
      echo "heartbeat_log=$LOG_DIR/heartbeat.log"
      echo "launchd_stdout=$LOG_DIR/launchd.stdout.log"
      echo "launchd_stderr=$LOG_DIR/launchd.stderr.log"
    fi
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage >&2
    exit 2
    ;;
esac
