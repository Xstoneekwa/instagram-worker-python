#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
python3 "$ROOT/scripts/verify-follow60-mainline-lock-v3.py"

if test "${1:-}" = "--execute"; then
  python3 "$ROOT/scripts/verify-follow60-physical-write-lock-v3-1.py" --target-root "$ROOT"
fi

LOCK_STATE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["lock_state"])' "$ROOT/docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json" 2>/dev/null || true)"
if test "$LOCK_STATE" = "UNLOCKED"; then
  printf '%s\n' "PACKAGE_WHILE_UNLOCKED=FAIL" >&2
  exit 4
fi

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
