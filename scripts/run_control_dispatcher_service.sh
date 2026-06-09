#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${RUN_CONTROL_DISPATCHER_ENV_FILE:-$ROOT_DIR/.env.run-control-dispatcher}"
LOG_DIR="${RUN_CONTROL_DISPATCHER_LOG_DIR:-$ROOT_DIR/logs/run-control-dispatcher}"

if [[ -f "$ENV_FILE" ]]; then
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

export RUN_CONTROL_DISPATCHER_ENABLED="${RUN_CONTROL_DISPATCHER_ENABLED:-true}"
export RUNTIME_HEARTBEATS_ENABLED="${RUNTIME_HEARTBEATS_ENABLED:-true}"
export RUN_CONTROL_DISPATCHER_HEALTH_ONLY="${RUN_CONTROL_DISPATCHER_HEALTH_ONLY:-false}"
export RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED="${RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED:-true}"
export RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE="${RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE:-false}"

if [[ -z "${RUN_CONTROL_DISPATCHER_WORKER_ID:-}" ]]; then
  HOST_NAME="$(hostname -s 2>/dev/null || hostname)"
  export RUN_CONTROL_DISPATCHER_WORKER_ID="run-dispatcher:${HOST_NAME}"
fi

mkdir -p "$LOG_DIR"

usage() {
  cat <<'EOF'
Usage: scripts/run_control_dispatcher_service.sh <command>

Commands:
  preflight   Read-only startup preflight (no heartbeat, no claim)
  start       Run dispatcher forever (blocks if launch preflight fails)
  once        Run one dispatcher loop iteration
  status      Print configured worker id and preflight summary
EOF
}

cmd="${1:-start}"
case "$cmd" in
  preflight)
    python3 account_run_request_consumer.py preflight
    ;;
  start)
    python3 account_run_request_consumer.py >>"$LOG_DIR/dispatcher.log" 2>&1
    ;;
  once)
    python3 account_run_request_consumer.py once
    ;;
  status)
    echo "worker_id=${RUN_CONTROL_DISPATCHER_WORKER_ID}"
    echo "health_only=${RUN_CONTROL_DISPATCHER_HEALTH_ONLY}"
    echo "launch_enabled=${RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED}"
    echo "allow_existing_queue=${RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE}"
    python3 account_run_request_consumer.py preflight
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
