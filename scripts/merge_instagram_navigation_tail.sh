#!/usr/bin/env sh
# Merge a saved tail into instagram_navigation.py after a truncate.
# Usage:
#   ./scripts/merge_instagram_navigation_tail.sh /path/to/tail_only.py
# The tail file must start at `def _vision_validation_post_follow_before_mute` (or the
# first line you want after the preserved head) and run through EOF of the full module.
#
# Default head line count matches the last known good gate boundary (vision candidate gate).
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TAIL_FILE="${1:?pass path to tail-only .py file}"
HEAD_LINES="${HEAD_LINES:-15744}"
test -f "$TAIL_FILE"
TS="$(date +%Y%m%d%H%M%S)"
cp -a instagram_navigation.py "instagram_navigation.py.bak.${TS}"
head -n "$HEAD_LINES" "instagram_navigation.py.bak.${TS}" > instagram_navigation.py.tmp
cat "$TAIL_FILE" >> instagram_navigation.py.tmp
mv instagram_navigation.py.tmp instagram_navigation.py
python3 -m py_compile instagram_navigation.py
python3 -c "from instagram_navigation import open_followers_list_from_profile; print('import_ok')"
