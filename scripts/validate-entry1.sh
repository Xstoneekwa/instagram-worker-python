#!/usr/bin/env bash
set -euo pipefail

ENTRY1_ACCOUNT_ID="${ENTRY1_ACCOUNT_ID:-42c625c2-e761-4100-8a9d-7ae1373de97d}"
OUTREACH_ENQUEUE_INTERNAL_API_TOKEN="${OUTREACH_ENQUEUE_INTERNAL_API_TOKEN:-}"

if [[ -z "${ENTRY1_BASE_URL:-}" ]]; then
  if [[ -z "${SUPABASE_URL:-}" ]]; then
    echo "FAIL missing ENTRY1_BASE_URL or SUPABASE_URL"
    exit 1
  fi
  ENTRY1_BASE_URL="${SUPABASE_URL%/}/functions/v1/outreach-enqueue"
fi

if [[ -z "$OUTREACH_ENQUEUE_INTERNAL_API_TOKEN" ]]; then
  echo "FAIL missing OUTREACH_ENQUEUE_INTERNAL_API_TOKEN"
  exit 1
fi

if [[ -z "$ENTRY1_ACCOUNT_ID" ]]; then
  echo "FAIL missing ENTRY1_ACCOUNT_ID"
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "FAIL curl is required"
  exit 1
fi

PASS_COUNT=0
FAIL_COUNT=0
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

redact() {
  local input="$1"
  if [[ -n "$OUTREACH_ENQUEUE_INTERNAL_API_TOKEN" ]]; then
    input="${input//$OUTREACH_ENQUEUE_INTERNAL_API_TOKEN/***}"
  fi
  printf '%s' "$input"
}

print_body() {
  local file="$1"
  if command -v jq >/dev/null 2>&1; then
    local formatted
    if formatted="$(jq . "$file" 2>/dev/null)"; then
      redact "$formatted"
      printf '\n'
    else
      redact "$(cat "$file")"
      printf '\n'
    fi
  else
    redact "$(cat "$file")"
    printf '\n'
  fi
}

record_pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  echo "PASS $1"
}

record_fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  echo "FAIL $1"
}

request() {
  local name="$1"
  local path="$2"
  local auth_mode="$3"
  local payload="$4"
  local body_file="$TMP_DIR/${name}.json"
  local url="${ENTRY1_BASE_URL%/}${path}"
  local -a headers=(-H "Content-Type: application/json")

  case "$auth_mode" in
    bearer)
      headers+=(-H "Authorization: Bearer ${OUTREACH_ENQUEUE_INTERNAL_API_TOKEN}")
      ;;
    wrong)
      headers+=(-H "Authorization: Bearer wrong-token-entry1-validation")
      ;;
    none)
      ;;
    *)
      echo "FAIL invalid auth mode: $auth_mode"
      exit 1
      ;;
  esac

  local http_status
  http_status="$(curl -sS -o "$body_file" -w "%{http_code}" -X POST "$url" "${headers[@]}" -d "$payload" || true)"

  echo
  echo "== $name =="
  echo "POST $(redact "$url")"
  echo "HTTP $http_status"
  print_body "$body_file"

  RESPONSE_BODY_FILE="$body_file"
  RESPONSE_HTTP_STATUS="$http_status"
}

assert_json() {
  local expression="$1"
  python3 - "$RESPONSE_BODY_FILE" "$expression" <<'PY'
import json
import sys

path, expression = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)

helpers = {"o": data, "any": any, "bool": bool, "len": len, "sum": sum}
if not eval(expression, {"__builtins__": {}}, helpers):
    raise SystemExit(1)
PY
}

expect_test() {
  local name="$1"
  local expected_statuses="$2"
  local expression="$3"

  if [[ ",${expected_statuses}," != *",${RESPONSE_HTTP_STATUS},"* ]]; then
    record_fail "$name expected HTTP ${expected_statuses}, got ${RESPONSE_HTTP_STATUS}"
    return
  fi

  if [[ -n "$expression" ]] && ! assert_json "$expression"; then
    record_fail "$name JSON assertion failed"
    return
  fi

  record_pass "$name"
}

echo "Entry 1 remote validation"
echo "ENTRY1_BASE_URL=$(redact "$ENTRY1_BASE_URL")"
echo "ENTRY1_ACCOUNT_ID=$ENTRY1_ACCOUNT_ID"
echo "OUTREACH_ENQUEUE_INTERNAL_API_TOKEN=set"

single_payload="$(cat <<JSON
{
  "account_id": "$ENTRY1_ACCOUNT_ID",
  "recipient_username": "entry1_single_test",
  "source": "dashboard",
  "message_body": "Hey 👋 test outreach API Entry 1",
  "metadata": { "external_request_id": "entry1-remote-single-test" }
}
JSON
)"

bulk_payload="$(cat <<JSON
{
  "account_id": "$ENTRY1_ACCOUNT_ID",
  "source": "campaign",
  "message_body": "Hey 👋 test outreach API Entry 1 bulk",
  "recipients": ["entry1_bulk_1", "entry1_bulk_2", "bad@@"],
  "metadata": { "import_id": "entry1-remote-bulk-test" }
}
JSON
)"

request "single_valid" "/outreach/enqueue" bearer "$single_payload"
expect_test \
  "single_valid" \
  "200,201" \
  "o.get('ok') is True and o.get('result') in ('created', 'duplicate_existing') and bool(o.get('job_id')) and o.get('status') == 'pending' and o.get('message_frozen') is True"

request "bulk_mixed" "/outreach/bulk-enqueue" bearer "$bulk_payload"
expect_test \
  "bulk_mixed" \
  "200,201" \
  "(o.get('summary') or {}).get('accepted', 0) + (o.get('summary') or {}).get('duplicates', 0) == 2 and (o.get('summary') or {}).get('rejected') == 1 and any((r.get('recipient_username') == 'bad@@' and r.get('ok') is False and r.get('error') == 'invalid_username') for r in o.get('results', []))"

request "reject_missing_authorization" "/outreach/enqueue" none "$single_payload"
expect_test "reject_missing_authorization" "401" "o.get('ok') is False and o.get('error') == 'unauthorized'"

request "reject_wrong_token" "/outreach/enqueue" wrong "$single_payload"
expect_test "reject_wrong_token" "401" "o.get('ok') is False and o.get('error') == 'unauthorized'"

welcome_scan_payload="$(python3 - <<PY
import json
payload = json.loads('''$single_payload''')
payload["source"] = "welcome_scan"
print(json.dumps(payload))
PY
)"
request "reject_source_welcome_scan" "/outreach/enqueue" bearer "$welcome_scan_payload"
expect_test "reject_source_welcome_scan" "400" "o.get('ok') is False and o.get('error') == 'source_not_allowed'"

handoff_payload="$(python3 - <<PY
import json
payload = json.loads('''$single_payload''')
payload["metadata"] = {"handoff": "unfollow"}
print(json.dumps(payload))
PY
)"
request "reject_metadata_handoff_unfollow" "/outreach/enqueue" bearer "$handoff_payload"
expect_test "reject_metadata_handoff_unfollow" "400" "o.get('ok') is False and o.get('error') == 'metadata_handoff_unfollow_forbidden'"

status_payload="$(python3 - <<PY
import json
payload = json.loads('''$single_payload''')
payload["status"] = "pending"
print(json.dumps(payload))
PY
)"
request "reject_forbidden_status" "/outreach/enqueue" bearer "$status_payload"
expect_test "reject_forbidden_status" "400" "o.get('ok') is False and o.get('error') == 'field_forbidden:status'"

bad_username_payload="$(python3 - <<PY
import json
payload = json.loads('''$single_payload''')
payload["recipient_username"] = "bad@@"
print(json.dumps(payload))
PY
)"
request "reject_invalid_username" "/outreach/enqueue" bearer "$bad_username_payload"
expect_test "reject_invalid_username" "400" "o.get('ok') is False and o.get('error') == 'invalid_username'"

import_csv_payload="$(python3 - <<PY
import json
payload = json.loads('''$single_payload''')
payload["source"] = "import_csv"
print(json.dumps(payload))
PY
)"
request "reject_import_csv" "/outreach/enqueue" bearer "$import_csv_payload"
expect_test "reject_import_csv" "400" "o.get('ok') is False and o.get('error') == 'source_import_csv_not_supported_use_campaign'"

echo
echo "Summary: PASS=$PASS_COUNT FAIL=$FAIL_COUNT"
if [[ "$FAIL_COUNT" -ne 0 ]]; then
  exit 1
fi
