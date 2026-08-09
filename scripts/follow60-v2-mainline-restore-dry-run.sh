#!/bin/sh
set -eu

MANIFEST="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/FOLLOW60_V2_MAINLINE_MANIFEST_V1.json"
test -f "$MANIFEST"
python3 "$(dirname -- "$0")/verify-follow60-mainline-lock-v2-1.py" \
  --target-root "$(dirname -- "$0")/.." --manifest "$MANIFEST"
printf '%s\n' "RESTORE_DRY_RUN_READY; no runtime mutation performed."
