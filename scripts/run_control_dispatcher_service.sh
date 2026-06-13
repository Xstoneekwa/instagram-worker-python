#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${RUN_CONTROL_DISPATCHER_ENV_FILE:-$ROOT_DIR/.env.run-control-dispatcher}"
LOG_DIR="${RUN_CONTROL_DISPATCHER_LOG_DIR:-$ROOT_DIR/logs/run-control-dispatcher}"
RUN_DIR="${RUN_CONTROL_DISPATCHER_RUN_DIR:-$ROOT_DIR/.local/run-control-dispatcher}"
LOCK_DIR="$RUN_DIR/dispatcher.lock"
PID_FILE="$RUN_DIR/dispatcher.pid"
PAUSE_FILE="$RUN_DIR/paused"
PLIST_SOURCE="$ROOT_DIR/ops/launchd/com.boost.phonefarm.dispatcher.plist"
PLIST_TARGET="$HOME/Library/LaunchAgents/com.boost.phonefarm.dispatcher.plist"
LAUNCHD_LABEL="com.boost.phonefarm.dispatcher"
LEGACY_LAUNCHD_LABEL="com.instagram.run-control-dispatcher"
LEGACY_PLIST_TARGET="$HOME/Library/LaunchAgents/com.instagram.run-control-dispatcher.plist"

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
  if [[ -n "${RUN_CONTROL_DISPATCHER_PYTHON:-}" && -x "${RUN_CONTROL_DISPATCHER_PYTHON}" ]]; then
    printf '%s' "${RUN_CONTROL_DISPATCHER_PYTHON}"
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

export RUN_CONTROL_DISPATCHER_ENABLED="${RUN_CONTROL_DISPATCHER_ENABLED:-true}"
export RUNTIME_HEARTBEATS_ENABLED="${RUNTIME_HEARTBEATS_ENABLED:-true}"
export RUN_CONTROL_DISPATCHER_HEALTH_ONLY="${RUN_CONTROL_DISPATCHER_HEALTH_ONLY:-false}"
export RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED="${RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED:-true}"
export RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE="${RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE:-false}"

if [[ -z "${RUN_CONTROL_DISPATCHER_WORKER_ID:-}" ]]; then
  HOST_NAME="$(hostname -s 2>/dev/null || hostname)"
  export RUN_CONTROL_DISPATCHER_WORKER_ID="run-dispatcher:${HOST_NAME}"
fi

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

_pid_is_consumer() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  local command_line
  command_line="$(_pid_command_line "$pid")"
  [[ "$command_line" == *"account_run_request_consumer.py"* ]]
}

_pid_is_wrapper() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  local command_line
  command_line="$(_pid_command_line "$pid")"
  [[ "$command_line" == *"run_control_dispatcher_service.sh start"* ]]
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

_kill_all_dispatcher_processes() {
  local pid
  while read -r pid; do
    if _pid_is_consumer "$pid"; then
      _kill_pid_gracefully "$pid"
    fi
  done < <(pgrep -f "account_run_request_consumer\\.py" 2>/dev/null || true)
  sleep 1
  while read -r pid; do
    if _pid_is_consumer "$pid" && _pid_alive "$pid"; then
      _kill_pid_force "$pid"
    fi
  done < <(pgrep -f "account_run_request_consumer\\.py" 2>/dev/null || true)
  while read -r pid; do
    if _pid_is_wrapper "$pid"; then
      _kill_pid_gracefully "$pid"
    fi
  done < <(pgrep -f "run_control_dispatcher_service\\.sh start" 2>/dev/null || true)
}

_clear_pid_and_lock() {
  rm -f "$PID_FILE"
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

_existing_dispatcher_pid() {
  if [[ -f "$PID_FILE" ]]; then
    local recorded
    recorded="$(tr -dc '0-9' < "$PID_FILE" || true)"
    if _pid_alive "$recorded" && _pid_is_consumer "$recorded"; then
      printf '%s' "$recorded"
      return 0
    fi
  fi
  local pid
  while read -r pid; do
    if _pid_is_consumer "$pid"; then
      printf '%s' "$pid"
      return 0
    fi
  done < <(pgrep -f "account_run_request_consumer\\.py" 2>/dev/null || true)
}

_dispatcher_process_count() {
  local count=0
  local pid
  while read -r pid; do
    if _pid_is_consumer "$pid"; then
      count=$((count + 1))
    fi
  done < <(pgrep -f "account_run_request_consumer\\.py" 2>/dev/null || true)
  printf '%s' "$count"
}

_list_consumer_pids() {
  local pid
  while read -r pid; do
    if _pid_is_consumer "$pid"; then
      printf '%s\n' "$pid"
    fi
  done < <(pgrep -f "account_run_request_consumer\\.py" 2>/dev/null || true)
}

_launchd_loaded() {
  launchctl print "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1
}

_status_json() {
  local paused="false"
  local existing_pid=""
  local process_count="0"
  local launchd_loaded="false"
  local preflight_json=""
  local preflight_exit="0"

  if [[ -f "$PAUSE_FILE" ]]; then
    paused="true"
  fi
  if existing_pid="$(_existing_dispatcher_pid)" && [[ -n "$existing_pid" ]]; then
    :
  else
    existing_pid=""
  fi
  process_count="$(_dispatcher_process_count)"
  if _launchd_loaded; then
    launchd_loaded="true"
  fi

  if [[ "$paused" == "true" ]]; then
    preflight_json='{"ok":false,"reason":"skipped_paused","active_count":0,"mode":"paused"}'
  else
    set +e
    preflight_json="$("$PYTHON_BIN" account_run_request_consumer.py preflight --json)"
    preflight_exit="$?"
    set -e
  fi

  DISPATCHER_STATUS_WORKER_ID="$RUN_CONTROL_DISPATCHER_WORKER_ID" \
  DISPATCHER_STATUS_HEALTH_ONLY="$RUN_CONTROL_DISPATCHER_HEALTH_ONLY" \
  DISPATCHER_STATUS_LAUNCH_ENABLED="$RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED" \
  DISPATCHER_STATUS_ALLOW_EXISTING_QUEUE="$RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE" \
  DISPATCHER_STATUS_PAUSED="$paused" \
  DISPATCHER_STATUS_PID="$existing_pid" \
  DISPATCHER_STATUS_PROCESS_COUNT="$process_count" \
  DISPATCHER_STATUS_CONSUMER_PIDS="$(paste -sd, <(_list_consumer_pids) 2>/dev/null || true)" \
  DISPATCHER_STATUS_LAUNCHD_LOADED="$launchd_loaded" \
  DISPATCHER_STATUS_PREFLIGHT_EXIT="$preflight_exit" \
  DISPATCHER_STATUS_PREFLIGHT_JSON="$preflight_json" \
  DISPATCHER_STATUS_LOG_DIR="$LOG_DIR" \
  DISPATCHER_STATUS_PID_FILE="$PID_FILE" \
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


def safe_preflight(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {"ok": False, "reason": "preflight_output_invalid"}
    except Exception:
        return {
            "ok": False,
            "reason": "preflight_json_parse_failed",
            "message": "Dispatcher preflight output was not valid JSON.",
        }


paused = as_bool(os.environ.get("DISPATCHER_STATUS_PAUSED", "false"))
pid = os.environ.get("DISPATCHER_STATUS_PID", "").strip()
process_count = as_int(os.environ.get("DISPATCHER_STATUS_PROCESS_COUNT", "0"))
consumer_pids_raw = os.environ.get("DISPATCHER_STATUS_CONSUMER_PIDS", "").strip()
consumer_pids = []
if consumer_pids_raw:
    for part in consumer_pids_raw.split(","):
        part = part.strip()
        if part.isdigit():
            consumer_pids.append(int(part))
process_running = bool(pid)
launchd_loaded = as_bool(os.environ.get("DISPATCHER_STATUS_LAUNCHD_LOADED", "false"))
launch_enabled = as_bool(os.environ.get("DISPATCHER_STATUS_LAUNCH_ENABLED", "true"))
preflight = safe_preflight(os.environ.get("DISPATCHER_STATUS_PREFLIGHT_JSON", ""))
preflight_ok = bool(preflight.get("ok"))

if paused:
    status = "paused"
elif process_running and process_count > 1:
    status = "unhealthy"
elif process_running and preflight_ok and launch_enabled:
    status = "running"
elif process_running:
    status = "unhealthy"
elif launchd_loaded:
    status = "starting"
else:
    status = "stopped"

last_error = ""
if not preflight_ok:
    last_error = str(preflight.get("reason") or preflight.get("error") or "").strip()
if process_count > 1:
    last_error = "duplicate_dispatcher_processes"

payload = {
    "ok": status in {"running", "paused", "stopped", "starting"},
    "status": status,
    "worker_id": os.environ.get("DISPATCHER_STATUS_WORKER_ID", ""),
    "dispatcher_id": os.environ.get("DISPATCHER_STATUS_WORKER_ID", ""),
    "paused": paused,
    "processRunning": process_running,
    "pid": as_int(pid, 0) if pid else None,
    "processCount": process_count,
    "consumerPids": consumer_pids,
    "duplicateProcess": process_count > 1,
    "launchdLoaded": launchd_loaded,
    "launchEnabled": launch_enabled,
    "healthOnly": as_bool(os.environ.get("DISPATCHER_STATUS_HEALTH_ONLY", "false")),
    "allowExistingQueue": as_bool(os.environ.get("DISPATCHER_STATUS_ALLOW_EXISTING_QUEUE", "false")),
    "preflight": preflight,
    "preflightOk": preflight_ok,
    "queueActiveCount": as_int(str(preflight.get("active_count", 0))),
    "lastError": last_error or None,
    "logsPath": os.path.join(os.environ.get("DISPATCHER_STATUS_LOG_DIR", ""), "dispatcher.log"),
    "pidFile": os.environ.get("DISPATCHER_STATUS_PID_FILE", ""),
    "checkedAt": datetime.now(timezone.utc).isoformat(),
    "message": {
        "running": "Dispatcher is healthy and ready.",
        "paused": "Dispatcher is paused. Resume it before starting Auto Login or runs.",
        "unhealthy": "Dispatcher is running but cannot process jobs.",
        "stopped": "Dispatcher is stopped.",
        "starting": "Dispatcher is starting or waiting for launchd.",
    }.get(status, "Dispatcher status unavailable."),
}
print(json.dumps(payload, sort_keys=True))
PY
}

_require_start_preconditions() {
  local missing=0
  if [[ ! -f "$ROOT_DIR/account_run_request_consumer.py" ]]; then
    echo "FAIL missing account_run_request_consumer.py in $ROOT_DIR" >&2
    missing=1
  fi
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "FAIL python not executable: $PYTHON_BIN" >&2
    missing=1
  fi
  if [[ -z "${SUPABASE_URL:-}" ]]; then
    echo "FAIL missing SUPABASE_URL in dispatcher env" >&2
    missing=1
  fi
  if [[ -z "${SUPABASE_SERVICE_ROLE_KEY:-}" ]]; then
    echo "FAIL missing SUPABASE_SERVICE_ROLE_KEY in dispatcher env" >&2
    missing=1
  fi
  if [[ -z "${ADB_PATH:-}" || ! -x "${ADB_PATH:-}" ]]; then
    echo "FAIL adb not found; set ADB_PATH or install Android platform-tools" >&2
    missing=1
  fi
  if [[ "$missing" != "0" ]]; then
    return 2
  fi
}

_start_foreground() {
  _require_start_preconditions
  if existing_pid="$(_existing_dispatcher_pid)"; then
    if [[ -n "$existing_pid" && "$existing_pid" != "$$" ]]; then
      echo "dispatcher_already_running pid=$existing_pid"
      exit 0
    fi
  fi
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    if existing_pid="$(_existing_dispatcher_pid)" && [[ -n "$existing_pid" && "$existing_pid" != "$$" ]]; then
      echo "dispatcher_lock_held path=$LOCK_DIR"
      exit 0
    fi
    rmdir "$LOCK_DIR" 2>/dev/null || true
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
      echo "dispatcher_lock_held path=$LOCK_DIR"
      exit 0
    fi
  fi
  trap _clear_pid_and_lock EXIT INT TERM
  _record_pid "$$"
  "$PYTHON_BIN" account_run_request_consumer.py >>"$LOG_DIR/dispatcher.log" 2>&1 &
  consumer_pid="$!"
  _record_pid "$consumer_pid"
  wait "$consumer_pid"
}

usage() {
  cat <<'EOF'
Usage: scripts/run_control_dispatcher_service.sh <command>

Commands:
  preflight   Read-only startup preflight (no heartbeat, no claim)
  start       Run dispatcher forever (blocks if launch preflight fails)
  once        Run one dispatcher loop iteration
  status      Print configured worker id and preflight summary
  stop        Stop the LaunchAgent or matching dispatcher process
  pause       Unload LaunchAgent and stop process (no auto-restart until resume)
  resume      Reload LaunchAgent after pause (idempotent when already healthy)
  restart     Stop then kickstart the LaunchAgent (clears pause)
  fix-duplicate Kill extra consumer processes and restart cleanly
  install     Install/load the user LaunchAgent
  launchd     Print launchd status for the dispatcher
  logs        Print dispatcher log paths
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
  preflight)
    if [[ "$json_mode" == "true" ]]; then
      "$PYTHON_BIN" account_run_request_consumer.py preflight --json
    else
      "$PYTHON_BIN" account_run_request_consumer.py preflight
    fi
    ;;
  start)
    _start_foreground
    ;;
  once)
    "$PYTHON_BIN" account_run_request_consumer.py once
    ;;
  status)
    if [[ "$json_mode" == "true" ]]; then
      _status_json
      exit 0
    fi
    echo "worker_id=${RUN_CONTROL_DISPATCHER_WORKER_ID}"
    echo "health_only=${RUN_CONTROL_DISPATCHER_HEALTH_ONLY}"
    echo "launch_enabled=${RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED}"
    echo "allow_existing_queue=${RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE}"
    if [[ -f "$PAUSE_FILE" ]]; then
      echo "paused=true"
    else
      echo "paused=false"
    fi
    echo "root_dir=${ROOT_DIR}"
    echo "python=${PYTHON_BIN}"
    echo "adb_path=${ADB_PATH:-missing}"
    if existing_pid="$(_existing_dispatcher_pid)" && [[ -n "$existing_pid" ]]; then
      echo "process=running pid=${existing_pid}"
    else
      echo "process=stopped"
    fi
    if [[ -f "$PAUSE_FILE" ]]; then
      echo "preflight=skipped_paused"
      exit 0
    fi
    "$PYTHON_BIN" account_run_request_consumer.py preflight
    ;;
  stop)
    launchctl stop "$LAUNCHD_LABEL" 2>/dev/null || true
    launchctl stop "$LEGACY_LAUNCHD_LABEL" 2>/dev/null || true
    _kill_all_dispatcher_processes
    echo "dispatcher_stop_requested"
    rm -f "$PID_FILE"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    ;;
  pause)
    touch "$PAUSE_FILE"
    launchctl bootout "gui/$(id -u)" "$PLIST_TARGET" >/dev/null 2>&1 || true
    launchctl bootout "gui/$(id -u)" "$LEGACY_PLIST_TARGET" >/dev/null 2>&1 || true
    _kill_all_dispatcher_processes
    echo "dispatcher_paused"
    rm -f "$PID_FILE"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    ;;
  resume)
    rm -f "$PAUSE_FILE"
    process_count="$(_dispatcher_process_count)"
    if [[ "$process_count" == "1" ]] && _launchd_loaded; then
      echo "dispatcher_already_healthy consumer_count=1"
      exit 0
    fi
    if [[ "$process_count" -gt "1" ]]; then
      _kill_all_dispatcher_processes
      sleep 1
      process_count="$(_dispatcher_process_count)"
    fi
    if [[ "$process_count" == "1" ]]; then
      if [[ ! -f "$PLIST_TARGET" ]]; then
        "$0" install
      elif ! _launchd_loaded; then
        launchctl bootstrap "gui/$(id -u)" "$PLIST_TARGET" 2>/dev/null || true
        launchctl enable "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
      fi
      echo "dispatcher_already_healthy consumer_count=1"
      exit 0
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
    echo "dispatcher_resumed label=$LAUNCHD_LABEL"
    ;;
  restart)
    rm -f "$PAUSE_FILE"
    launchctl bootout "gui/$(id -u)" "$LEGACY_PLIST_TARGET" >/dev/null 2>&1 || true
    _kill_all_dispatcher_processes
    sleep 1
    if [[ ! -f "$PLIST_TARGET" ]]; then
      "$0" install
    else
      launchctl bootstrap "gui/$(id -u)" "$PLIST_TARGET" 2>/dev/null || true
      launchctl enable "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
    fi
    launchctl kickstart -k "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
    echo "dispatcher_restart_requested label=$LAUNCHD_LABEL"
    ;;
  fix-duplicate)
    rm -f "$PAUSE_FILE"
    launchctl bootout "gui/$(id -u)" "$LEGACY_PLIST_TARGET" >/dev/null 2>&1 || true
    rm -f "$LEGACY_PLIST_TARGET"
    _kill_all_dispatcher_processes
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
    echo "dispatcher_duplicate_fixed label=$LAUNCHD_LABEL"
    ;;
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
    cp "$PLIST_SOURCE" "$PLIST_TARGET"
    chmod 644 "$PLIST_TARGET"
    launchctl bootout "gui/$(id -u)" "$LEGACY_PLIST_TARGET" >/dev/null 2>&1 || true
    rm -f "$LEGACY_PLIST_TARGET"
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
      echo "$LOG_DIR/dispatcher.log"
    else
      echo "dispatcher_log=$LOG_DIR/dispatcher.log"
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
