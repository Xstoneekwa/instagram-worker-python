#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/check_golden_flow_untouched.sh [options]

Checks that modern worker Golden Flow protected files are untouched.

Options:
  --manifest PATH                  Manifest path.
  --baseline-ref REF               Git ref to compare against (default: HEAD).
  --allow-worker-golden-touch      Allow protected changes after explicit Liam GO.
  --simulate-touch PATH            Simulate a protected file change without editing files.
  -h, --help                       Show this help.

Environment:
  ALLOW_WORKER_GOLDEN_TOUCH=1      Equivalent to --allow-worker-golden-touch.
EOF
}

MANIFEST="docs/golden-flow/locked_files_manifest.json"
BASELINE_REF="HEAD"
ALLOW_TOUCH="${ALLOW_WORKER_GOLDEN_TOUCH:-0}"
SIMULATE_TOUCH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest)
      MANIFEST="${2:-}"
      shift 2
      ;;
    --baseline-ref)
      BASELINE_REF="${2:-}"
      shift 2
      ;;
    --allow-worker-golden-touch)
      ALLOW_TOUCH="1"
      shift
      ;;
    --simulate-touch)
      SIMULATE_TOUCH="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if [[ ! -f "$MANIFEST" ]]; then
  echo "Golden Flow guard manifest not found: $MANIFEST" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

protected_files_raw="$("$PYTHON_BIN" - "$MANIFEST" <<'PY'
import json
import sys

manifest_path = sys.argv[1]
with open(manifest_path, "r", encoding="utf-8") as fh:
    data = json.load(fh)

for item in data.get("protected_files", []):
    path = str(item.get("path") or "").strip()
    if path:
        print(path)
PY
)"

protected_files=()
while IFS= read -r path; do
  [[ -n "$path" ]] && protected_files+=("$path")
done <<< "$protected_files_raw"

if [[ ${#protected_files[@]} -eq 0 ]]; then
  echo "Golden Flow guard manifest has no protected files: $MANIFEST" >&2
  exit 2
fi

if ! git rev-parse --verify --quiet "$BASELINE_REF" >/dev/null; then
  echo "Baseline ref not found: $BASELINE_REF" >&2
  exit 2
fi

status_changed="$(git status --porcelain -- "${protected_files[@]}" || true)"
diff_changed="$(git diff --name-only "$BASELINE_REF" -- "${protected_files[@]}" || true)"
changed_files="$(printf '%s\n%s\n' "$status_changed" "$diff_changed" \
  | awk 'NF { if (length($0) > 3 && substr($0, 3, 1) == " ") print substr($0, 4); else print $0 }' \
  | sort -u)"

if [[ -n "$SIMULATE_TOUCH" ]]; then
  is_protected="0"
  for path in "${protected_files[@]}"; do
    if [[ "$path" == "$SIMULATE_TOUCH" ]]; then
      is_protected="1"
      break
    fi
  done
  if [[ "$is_protected" != "1" ]]; then
    echo "Simulated touch is not in protected manifest: $SIMULATE_TOUCH" >&2
    exit 2
  fi
  changed_files="$(printf '%s\n%s\n' "$changed_files" "$SIMULATE_TOUCH" | awk 'NF' | sort -u)"
fi

if [[ -n "$changed_files" && "$ALLOW_TOUCH" != "1" ]]; then
  cat <<EOF
Golden Flow protected area touched. STOP unless explicit Liam GO.

Manifest: $MANIFEST
Baseline: $BASELINE_REF

Protected files changed:
$changed_files

Use --allow-worker-golden-touch only after explicit approval.
EOF
  exit 1
fi

echo "Golden Flow guard OK."
echo "Manifest: $MANIFEST"
echo "Baseline: $BASELINE_REF"

if [[ -n "$changed_files" ]]; then
  echo "Protected files touched but allowed:"
  echo "$changed_files"
fi
