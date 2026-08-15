#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
python3 "$ROOT/scripts/verify-follow60-mainline-lock-v3.py"

SHARED_TOKEN="/Users/admin/phonefarm-runtime/run/run-control-dispatcher/auto-restart-skip-startup-tick.once"
RELEASE="/Users/admin/phonefarm-worker-releases/FOLLOW60_V2_MAINLINE_V1"

if test "${1:-}" != "--execute"; then
  printf '%s\n' "DRY_RUN shared_startup_skip=$SHARED_TOKEN release=$RELEASE"
  printf '%s\n' "No token, switch, restart, run, tick or ADB action performed."
  exit 0
fi

printf '%s\n' "Execution is intentionally delegated to phonefarm-runtimectl after a fresh zero gate."
printf '%s\n' "This script never creates a release-local .local startup token."
exit 3
