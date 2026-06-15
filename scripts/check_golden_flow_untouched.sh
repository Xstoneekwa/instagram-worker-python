#!/usr/bin/env bash
set -euo pipefail

GOLDEN_REF="${GOLDEN_FLOW_REF:-golden-follow-mute-like-returnct-110s-20260615}"
ALLOW_TOUCH="${ALLOW_GOLDEN_FLOW_TOUCH:-0}"
FORBIDDEN_MARKER="stale_candidate_action_bar_ignored_with_strong_list"

protected_files=(
  "follow_action_engine.py"
  "instagram_navigation.py"
  "tests/test_post_follow_like_samsung_fast.py"
  "tests/test_mute_engine_v2_sheet_levels.py"
  "tests/test_follow_targets_runtime_p1b.py"
  "tests/test_runtime_caps.py"
)

if git rev-parse --verify --quiet "$GOLDEN_REF" >/dev/null; then
  diff_base="$GOLDEN_REF"
else
  diff_base="HEAD"
fi

changed_files="$(git diff --name-only "$diff_base" -- "${protected_files[@]}" || true)"
if [[ -n "$changed_files" && "$ALLOW_TOUCH" != "1" ]]; then
  echo "Golden flow protected area touched. STOP unless explicit GO."
  echo "Base: $diff_base"
  echo "Protected files modified:"
  echo "$changed_files"
  echo "Set ALLOW_GOLDEN_FLOW_TOUCH=1 only after explicit GO."
  exit 1
fi

if git grep -n "$FORBIDDEN_MARKER" -- "${protected_files[@]}" >/tmp/golden_flow_forbidden_marker.$$ 2>/dev/null; then
  echo "Forbidden marker present: $FORBIDDEN_MARKER"
  cat /tmp/golden_flow_forbidden_marker.$$
  rm -f /tmp/golden_flow_forbidden_marker.$$
  exit 1
fi
rm -f /tmp/golden_flow_forbidden_marker.$$

echo "Golden flow guard OK."
if [[ -n "$changed_files" ]]; then
  echo "Protected files touched but allowed by ALLOW_GOLDEN_FLOW_TOUCH=1:"
  echo "$changed_files"
fi
