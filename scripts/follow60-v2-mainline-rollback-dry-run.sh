#!/bin/sh
set -eu

ROLLBACK_SHA="45de130d29a8658ff24f222595f1d2b9184716d9"
ROLLBACK_RELEASE="/Users/admin/phonefarm-worker-releases/45de130-follow60-v1-verified-rollback-v1"

test -d "$ROLLBACK_RELEASE"
test "$(git -C "$ROLLBACK_RELEASE" rev-parse HEAD)" = "$ROLLBACK_SHA"
test "$(git -C "$ROLLBACK_RELEASE" status --porcelain | wc -l | tr -d ' ')" = "0"
printf '%s\n' "ROLLBACK_DRY_RUN_READY sha=$ROLLBACK_SHA release=$ROLLBACK_RELEASE"
printf '%s\n' "No switch, restart, run, tick, DB mutation or ADB action was performed."
