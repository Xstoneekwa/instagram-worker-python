#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-}"
WRAPPER_PATH="${2:-}"

if [[ -z "$ROOT_DIR" || -z "$WRAPPER_PATH" ]]; then
  echo "STOP reason=critical_file_invalid"
  echo "file=local_preflight"
  echo "detail=usage"
  exit 10
fi

stop_critical_file_invalid() {
  local file="$1"
  local detail="$2"
  echo "STOP reason=critical_file_invalid"
  echo "file=${file}"
  echo "detail=${detail}"
  exit 10
}

check_nonempty_file() {
  local rel="$1"
  local path="$ROOT_DIR/$rel"
  if [[ ! -f "$path" || ! -s "$path" ]]; then
    if [[ "$rel" == "config.py" ]]; then
      stop_critical_file_invalid "$rel" "missing_or_empty_or_missing_INSTAGRAM_PACKAGE"
    fi
    stop_critical_file_invalid "$rel" "missing_or_empty"
  fi
}

check_nonempty_file "config.py"
check_nonempty_file "runner.py"
check_nonempty_file "instagram_navigation.py"
check_nonempty_file "account_identity_guard.py"
check_nonempty_file "account_session_orchestrator.py"

if [[ ! -f "$WRAPPER_PATH" || ! -s "$WRAPPER_PATH" ]]; then
  stop_critical_file_invalid "$WRAPPER_PATH" "missing_or_empty"
fi

set +e
(
  cd "$ROOT_DIR" &&
    python3 -c "import config; assert hasattr(config, 'INSTAGRAM_PACKAGE') and str(config.INSTAGRAM_PACKAGE or '').strip(); print('config OK', config.INSTAGRAM_PACKAGE)"
) >/dev/null 2>&1
CONFIG_EC=$?
set -e
if [[ "$CONFIG_EC" -ne 0 ]]; then
  stop_critical_file_invalid "config.py" "missing_or_empty_or_missing_INSTAGRAM_PACKAGE"
fi

set +e
(
  cd "$ROOT_DIR" &&
    python3 -c "from account_identity_guard import ACCOUNT_IDENTITY_MISMATCH_REASON; assert str(ACCOUNT_IDENTITY_MISMATCH_REASON or '').strip(); print('account_identity_guard OK')"
) >/dev/null 2>&1
IDENTITY_GUARD_EC=$?
set -e
if [[ "$IDENTITY_GUARD_EC" -ne 0 ]]; then
  stop_critical_file_invalid "account_identity_guard.py" "missing_or_empty_or_missing_ACCOUNT_IDENTITY_MISMATCH_REASON"
fi

set +e
(
  cd "$ROOT_DIR" &&
    python3 -c "import runner, config; print('runner import OK')"
) >/dev/null 2>&1
RUNNER_EC=$?
set -e
if [[ "$RUNNER_EC" -ne 0 ]]; then
  stop_critical_file_invalid "runner.py" "runner_or_config_import_failed"
fi

exit 0
