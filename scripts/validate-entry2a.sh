#!/usr/bin/env bash
set -euo pipefail

# Entry 2A remote checks.
#
# Default mode (edge): Edge Function curls only. Requires SQL seed from
# docs/outreach-entry-api.md (checkpoint proof). PostgREST POST is skipped because
# the linked project returns PGRST102 even for manual curl with valid JSON.
#
# Optional mode (rest): ENTRY2A_REST_ENABLED=1 also runs PostgREST seed/helpers/PATCH
# (diagnostic only; not checkpoint proof on this project).
SCRIPT_VERSION="entry2a-validate-20260524-checkpoint-edge"
ENTRY2A_REST_ENABLED="${ENTRY2A_REST_ENABLED:-0}"
ENTRY2A_RUN_DISABLED_TEST="${ENTRY2A_RUN_DISABLED_TEST:-0}"

ENTRY2A_ACCOUNT_ID="${ENTRY2A_ACCOUNT_ID:-42c625c2-e761-4100-8a9d-7ae1373de97d}"
ENTRY2A_CLIENT_ID="${ENTRY2A_CLIENT_ID:-00000000-0000-4000-8000-000000002e2a}"
ENTRY2A_CLIENT_ACCOUNT_ID="${ENTRY2A_CLIENT_ACCOUNT_ID:-00000000-0000-4000-8000-00000012e2a1}"
ENTRY2A_ENTITLEMENT_ID="${ENTRY2A_ENTITLEMENT_ID:-00000000-0000-4000-8000-00000012e2a2}"
OUTREACH_ENQUEUE_INTERNAL_API_TOKEN="${OUTREACH_ENQUEUE_INTERNAL_API_TOKEN:-}"
SUPABASE_SERVICE_ROLE_KEY="${SUPABASE_SERVICE_ROLE_KEY:-}"

# Python payload builders read these via os.environ.
export ENTRY2A_ACCOUNT_ID ENTRY2A_CLIENT_ID ENTRY2A_CLIENT_ACCOUNT_ID ENTRY2A_ENTITLEMENT_ID

if [[ -z "${ENTRY2A_BASE_URL:-}" ]]; then
  if [[ -z "${SUPABASE_URL:-}" ]]; then
    echo "FAIL missing ENTRY2A_BASE_URL or SUPABASE_URL"
    exit 1
  fi
  ENTRY2A_BASE_URL="${SUPABASE_URL%/}/functions/v1/outreach-enqueue"
fi

if [[ -z "${SUPABASE_URL:-}" ]]; then
  echo "FAIL missing SUPABASE_URL"
  exit 1
fi
if [[ "$ENTRY2A_REST_ENABLED" == "1" ]] && [[ -z "$SUPABASE_SERVICE_ROLE_KEY" ]]; then
  echo "FAIL missing SUPABASE_SERVICE_ROLE_KEY (required when ENTRY2A_REST_ENABLED=1)"
  exit 1
fi
if [[ -z "$OUTREACH_ENQUEUE_INTERNAL_API_TOKEN" ]]; then
  echo "FAIL missing OUTREACH_ENQUEUE_INTERNAL_API_TOKEN"
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "FAIL curl is required"
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "FAIL python3 is required"
  exit 1
fi

PASS_COUNT=0
FAIL_COUNT=0
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

redact() {
  local input="$1"
  input="${input//$OUTREACH_ENQUEUE_INTERNAL_API_TOKEN/***}"
  input="${input//$SUPABASE_SERVICE_ROLE_KEY/***}"
  printf '%s' "$input"
}

print_body() {
  local file="$1"
  if command -v jq >/dev/null 2>&1; then
    local formatted
    if formatted="$(jq . "$file" 2>/dev/null)"; then
      redact "$formatted"
      printf '\n'
      return
    fi
  fi
  redact "$(cat "$file")"
  printf '\n'
}

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  echo "PASS $1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  echo "FAIL $1"
}

fail_local() {
  fail "$1"
  exit 1
}

# Validate payload file before any HTTP call. Aborts script on failure.
assert_payload_file() {
  local label="$1"
  local payload_file="$2"
  local min_bytes="${3:-3}"

  if [[ ! -f "$payload_file" ]]; then
    fail_local "${label}: payload file missing (${payload_file})"
  fi

  local nbytes
  nbytes="$(wc -c <"$payload_file" | tr -d ' ')"
  if [[ -z "$nbytes" ]] || [[ "$nbytes" -lt "$min_bytes" ]]; then
    fail_local "${label}: payload file empty or too small (${nbytes} bytes, min ${min_bytes})"
  fi

  if ! python3 -m json.tool "$payload_file" >/dev/null 2>&1; then
    echo "FAIL ${label}: payload is not valid JSON"
    echo "payload_file=${payload_file} bytes=${nbytes}"
    print_body "$payload_file"
    exit 1
  fi

  if [[ "${DEBUG:-0}" == "1" ]]; then
    echo "DEBUG ${label}"
    echo "DEBUG   payload_file=${payload_file}"
    echo "DEBUG   payload_bytes=${nbytes}"
    echo "DEBUG   payload_json=$(redact "$(python3 -m json.tool "$payload_file" 2>/dev/null || cat "$payload_file")")"
  fi
}

debug_curl() {
  local label="$1"
  local method="$2"
  local url="$3"
  local payload_file="$4"
  local payload_bytes="$5"
  shift 5
  if [[ "${DEBUG:-0}" != "1" ]]; then
    return
  fi
  echo "DEBUG ${label} curl"
  echo "DEBUG   method=${method}"
  echo "DEBUG   url=$(redact "$url")"
  echo "DEBUG   payload_file=${payload_file}"
  echo "DEBUG   payload_bytes=${payload_bytes}"
  echo "DEBUG   headers=Content-Type:application/json apikey:*** Authorization:Bearer:*** Accept:application/json $*"
}

# Read validated JSON from disk into memory for curl (avoids @file quirks).
read_payload_data() {
  local payload_file="$1"
  assert_payload_file "read_payload_data" "$payload_file"
  PAYLOAD_DATA="$(<"$payload_file")"
  if [[ -z "$PAYLOAD_DATA" ]]; then
    fail_local "read_payload_data: payload read empty after validation"
  fi
  PAYLOAD_BYTES="${#PAYLOAD_DATA}"
  if [[ "$PAYLOAD_BYTES" -lt 3 ]]; then
    fail_local "read_payload_data: payload string too short (${PAYLOAD_BYTES} bytes)"
  fi
}

# Write JSON payload via python stdin (never pass JSON through bash argv).
write_payload_from_python() {
  local payload_file="$1"
  python3 - "$payload_file" <<'PY'
import json
import os
import sys

out_path = sys.argv[1]
kind = os.environ["PAYLOAD_KIND"]

if kind == "seed_clients":
    payload = [
        {
            "id": os.environ["ENTRY2A_CLIENT_ID"],
            "name": "Entry 2A Test Client",
            "status": "active",
            "metadata": {"source": "entry2a-validate"},
        }
    ]
elif kind == "seed_client_instagram_accounts":
    payload = [
        {
            "id": os.environ["ENTRY2A_CLIENT_ACCOUNT_ID"],
            "client_id": os.environ["ENTRY2A_CLIENT_ID"],
            "account_id": os.environ["ENTRY2A_ACCOUNT_ID"],
            "label": "Entry 2A Test Account",
            "onboarding_status": "ready",
            "provisioning_status": "ready",
            "login_status": "connected",
        }
    ]
elif kind == "seed_client_entitlements":
    payload = [
        {
            "id": os.environ["ENTRY2A_ENTITLEMENT_ID"],
            "client_id": os.environ["ENTRY2A_CLIENT_ID"],
            "feature_code": "outreach",
            "entitlement_type": "standalone",
            "active": True,
            "metadata": {"source": "entry2a-validate"},
        }
    ]
elif kind == "rpc_account":
    payload = {"p_account_id": os.environ["RPC_ACCOUNT_ID"]}
elif kind == "patch_active":
    payload = {"active": os.environ["PATCH_ACTIVE"].lower() == "true"}
else:
    raise SystemExit(f"unknown PAYLOAD_KIND={kind!r}")

with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, separators=(",", ":"))
    fh.write("\n")
PY
}

write_edge_payload() {
  local payload_file="$1"
  local kind="$2"
  python3 - "$payload_file" "$kind" <<'PY'
import json
import os
import sys

out_path, kind = sys.argv[1], sys.argv[2]

if kind == "enqueue_created":
    payload = {
        "account_id": os.environ["ENTRY2A_ACCOUNT_ID"],
        "recipient_username": os.environ["EDGE_USERNAME"],
        "source": "dashboard",
        "message_body": "Hey test outreach API Entry 2A ownership",
        "metadata": {"external_request_id": os.environ["EDGE_REQUEST_ID"]},
    }
elif kind == "enqueue_disabled":
    payload = {
        "account_id": os.environ["ENTRY2A_ACCOUNT_ID"],
        "recipient_username": "entry2a_disabled_test",
        "source": "dashboard",
        "message_body": "Should be rejected by Entry 2A entitlement",
        "metadata": {"external_request_id": "entry2a-disabled-entitlement-test"},
    }
elif kind == "enqueue_wrong_account":
    payload = {
        "account_id": os.environ["RANDOM_ACCOUNT_ID"],
        "recipient_username": "entry2a_wrong_account",
        "source": "dashboard",
        "message_body": "Should be rejected by Entry 2A ownership",
        "metadata": {"external_request_id": "entry2a-wrong-account-test"},
    }
else:
    raise SystemExit(f"unknown edge kind={kind!r}")

with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, separators=(",", ":"))
    fh.write("\n")
PY
}

prepare_payload() {
  local label="$1"
  local payload_file="$2"
  local kind="$3"
  export PAYLOAD_KIND="$kind"
  write_payload_from_python "$payload_file"
  assert_payload_file "$label" "$payload_file"
}

# Upsert: POST array body + merge-duplicates Prefer only here.
rest_upsert() {
  local table="$1"
  local kind="$2"
  local test_name="${3:-seed_${table}}"
  local payload_file="$TMP_DIR/${test_name}.payload.json"
  local out="$TMP_DIR/${test_name}.response.json"
  local url="${SUPABASE_URL%/}/rest/v1/${table}"

  prepare_payload "$test_name" "$payload_file" "$kind"
  read_payload_data "$payload_file"

  debug_curl "$test_name" POST "$url" "$payload_file" "$PAYLOAD_BYTES" "Prefer:resolution=merge-duplicates,return=representation"

  REST_HTTP_STATUS="$(
    curl -sS -o "$out" -w "%{http_code}" -X POST "$url" \
      -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
      -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}" \
      -H "Accept: application/json" \
      -H "Content-Type: application/json" \
      -H "Prefer: resolution=merge-duplicates,return=representation" \
      --data-binary "$PAYLOAD_DATA" || true
  )"
  REST_BODY_FILE="$out"
  echo
  echo "== $test_name =="
  echo "POST $(redact "$url")"
  echo "payload_bytes=${PAYLOAD_BYTES}"
  echo "HTTP $REST_HTTP_STATUS"
  print_body "$REST_BODY_FILE"
}

# PATCH: object body, no merge-duplicates Prefer.
rest_patch() {
  local path="$1"
  local active="$2"
  local test_name="${3:-rest_patch}"
  local payload_file="$TMP_DIR/${test_name}.payload.json"
  local out="$TMP_DIR/${test_name}.response.json"
  local url="${SUPABASE_URL%/}${path}"

  export PATCH_ACTIVE="$active"
  export PAYLOAD_KIND="patch_active"
  write_payload_from_python "$payload_file"
  read_payload_data "$payload_file"

  debug_curl "$test_name" PATCH "$url" "$payload_file" "$PAYLOAD_BYTES" "Prefer:return=representation"

  REST_HTTP_STATUS="$(
    curl -sS -o "$out" -w "%{http_code}" -X PATCH "$url" \
      -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
      -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}" \
      -H "Accept: application/json" \
      -H "Content-Type: application/json" \
      -H "Prefer: return=representation" \
      --data-binary "$PAYLOAD_DATA" || true
  )"
  REST_BODY_FILE="$out"
  echo
  echo "== $test_name =="
  echo "PATCH $(redact "$url")"
  echo "payload_bytes=${PAYLOAD_BYTES}"
  echo "HTTP $REST_HTTP_STATUS"
  print_body "$REST_BODY_FILE"
}

# RPC: object body, no merge-duplicates Prefer.
rest_rpc() {
  local fn="$1"
  local account_id="$2"
  local test_name="${3:-rest_rpc_${fn}}"
  local payload_file="$TMP_DIR/${test_name}.payload.json"
  local out="$TMP_DIR/${test_name}.response.json"
  local url="${SUPABASE_URL%/}/rest/v1/rpc/${fn}"

  export RPC_ACCOUNT_ID="$account_id"
  export PAYLOAD_KIND="rpc_account"
  write_payload_from_python "$payload_file"
  read_payload_data "$payload_file"

  debug_curl "$test_name" POST "$url" "$payload_file" "$PAYLOAD_BYTES"

  REST_HTTP_STATUS="$(
    curl -sS -o "$out" -w "%{http_code}" -X POST "$url" \
      -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
      -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}" \
      -H "Accept: application/json" \
      -H "Content-Type: application/json" \
      --data-binary "$PAYLOAD_DATA" || true
  )"
  REST_BODY_FILE="$out"
  echo
  echo "== $test_name =="
  echo "POST $(redact "$url")"
  echo "payload_bytes=${PAYLOAD_BYTES}"
  echo "HTTP $REST_HTTP_STATUS"
  print_body "$REST_BODY_FILE"
}

edge_request() {
  local name="$1"
  local path="$2"
  local kind="$3"
  local payload_file="$TMP_DIR/${name}.payload.json"
  local out="$TMP_DIR/${name}.response.json"
  local url="${ENTRY2A_BASE_URL%/}${path}"

  write_edge_payload "$payload_file" "$kind"
  read_payload_data "$payload_file"

  debug_curl "$name" POST "$url" "$payload_file" "$PAYLOAD_BYTES" "Authorization:Bearer:***"

  EDGE_HTTP_STATUS="$(
    curl -sS -o "$out" -w "%{http_code}" -X POST "$url" \
      -H "Authorization: Bearer ${OUTREACH_ENQUEUE_INTERNAL_API_TOKEN}" \
      -H "Content-Type: application/json" \
      --data-binary "$PAYLOAD_DATA" || true
  )"
  EDGE_BODY_FILE="$out"
  echo
  echo "== $name =="
  echo "POST $(redact "$url")"
  echo "payload_bytes=${PAYLOAD_BYTES}"
  echo "HTTP $EDGE_HTTP_STATUS"
  print_body "$EDGE_BODY_FILE"
}

assert_json() {
  local file="$1"
  local expression="$2"
  python3 - "$file" "$expression" <<'PY'
import json
import sys

path, expression = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)

helpers = {"o": data, "any": any, "bool": bool, "len": len}
if not eval(expression, {"__builtins__": {}}, helpers):
    raise SystemExit(1)
PY
}

expect_rest() {
  local name="$1"
  local statuses="$2"
  if [[ ",${statuses}," == *",${REST_HTTP_STATUS},"* ]]; then
    pass "$name"
  else
    fail "$name expected HTTP ${statuses}, got ${REST_HTTP_STATUS}"
    print_body "$REST_BODY_FILE"
  fi
}

expect_edge() {
  local name="$1"
  local statuses="$2"
  local expression="$3"
  if [[ ",${statuses}," != *",${EDGE_HTTP_STATUS},"* ]]; then
    fail "$name expected HTTP ${statuses}, got ${EDGE_HTTP_STATUS}"
    return
  fi
  if [[ -n "$expression" ]] && ! assert_json "$EDGE_BODY_FILE" "$expression"; then
    fail "$name JSON assertion failed"
    return
  fi
  pass "$name"
}

skip_rest() {
  echo "SKIP $1 (PostgREST REST not checkpoint proof; use SQL seed in docs/outreach-entry-api.md)"
}

echo "Entry 2A remote validation (${SCRIPT_VERSION})"
echo "script_path=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
echo "mode=$([[ "$ENTRY2A_REST_ENABLED" == "1" ]] && echo rest+edge || echo edge-only)"
echo "SUPABASE_URL=set"
if [[ -n "$SUPABASE_SERVICE_ROLE_KEY" ]]; then
  echo "SUPABASE_SERVICE_ROLE_KEY=set"
else
  echo "SUPABASE_SERVICE_ROLE_KEY=unset (ok for edge-only mode)"
fi
echo "OUTREACH_ENQUEUE_INTERNAL_API_TOKEN=set"
echo "ENTRY2A_BASE_URL=$(redact "$ENTRY2A_BASE_URL")"
echo "ENTRY2A_ACCOUNT_ID=$ENTRY2A_ACCOUNT_ID"
echo "NOTE: SQL seed + helper checks are the Entry 2A checkpoint proof."
echo "NOTE: This script defaults to Edge Function curls after manual SQL seed."

RANDOM_ACCOUNT_ID="00000000-0000-4000-8000-000000000999"
export RANDOM_ACCOUNT_ID

if [[ "$ENTRY2A_REST_ENABLED" == "1" ]]; then
  echo
  echo "== PostgREST seed/helpers (diagnostic; may fail with PGRST102) =="
  rest_upsert "clients" "seed_clients" "seed_clients"
  expect_rest "seed_clients" "200,201"
  rest_upsert "client_instagram_accounts" "seed_client_instagram_accounts" "seed_client_instagram_accounts"
  expect_rest "seed_client_instagram_accounts" "200,201"
  rest_upsert "client_entitlements" "seed_client_entitlements" "seed_client_entitlements"
  expect_rest "seed_client_entitlements" "200,201"
  rest_rpc "client_account_has_outreach_entitlement" "$ENTRY2A_ACCOUNT_ID" "helper_true_for_seeded_account"
  if [[ "$REST_HTTP_STATUS" == "200" ]] && assert_json "$REST_BODY_FILE" "o is True"; then
    pass "helper_true_for_seeded_account"
  else
    fail "helper_true_for_seeded_account"
    print_body "$REST_BODY_FILE"
  fi
  rest_rpc "client_account_has_outreach_entitlement" "$RANDOM_ACCOUNT_ID" "helper_false_for_random_account"
  if [[ "$REST_HTTP_STATUS" == "200" ]] && assert_json "$REST_BODY_FILE" "o is False"; then
    pass "helper_false_for_random_account"
  else
    fail "helper_false_for_random_account"
    print_body "$REST_BODY_FILE"
  fi
else
  skip_rest "seed_clients"
  skip_rest "seed_client_instagram_accounts"
  skip_rest "seed_client_entitlements"
  skip_rest "helper_true_for_seeded_account"
  skip_rest "helper_false_for_random_account"
fi

echo
echo "== Edge Function entitlement guard =="

edge_request "reject_wrong_account" "/outreach/enqueue" "enqueue_wrong_account"
expect_edge "reject_wrong_account" "403" "o.get('ok') is False and o.get('error') == 'account_outreach_entitlement_required'"

export EDGE_USERNAME="entry2a_created_$(date -u +%Y%m%d%H%M%S)"
export EDGE_REQUEST_ID="entry1-created-${EDGE_USERNAME#entry2a_created_}"
edge_request "enqueue_with_db_entitlement" "/outreach/enqueue" "enqueue_created"
expect_edge "enqueue_with_db_entitlement" "200,201" "o.get('ok') is True and o.get('result') in ('created', 'duplicate_existing') and bool(o.get('job_id')) and o.get('status') == 'pending' and o.get('message_frozen') is True"

if [[ "$ENTRY2A_RUN_DISABLED_TEST" == "1" ]]; then
  echo
  echo "== disabled entitlement rejection (requires SQL: active=false on test entitlement) =="
  edge_request "reject_disabled_entitlement" "/outreach/enqueue" "enqueue_disabled"
  expect_edge "reject_disabled_entitlement" "403" "o.get('ok') is False and o.get('error') == 'account_outreach_entitlement_required'"
else
  echo "SKIP reject_disabled_entitlement (set ENTRY2A_RUN_DISABLED_TEST=1 after SQL disables entitlement)"
fi

if [[ "$ENTRY2A_REST_ENABLED" == "1" ]]; then
  echo
  echo "== PostgREST disable/restore (diagnostic) =="
  rest_patch "/rest/v1/client_entitlements?id=eq.${ENTRY2A_ENTITLEMENT_ID}" "false" "disable_entitlement"
  expect_rest "disable_entitlement" "200,204"
  rest_rpc "client_account_has_outreach_entitlement" "$ENTRY2A_ACCOUNT_ID" "helper_false_when_entitlement_disabled"
  if [[ "$REST_HTTP_STATUS" == "200" ]] && assert_json "$REST_BODY_FILE" "o is False"; then
    pass "helper_false_when_entitlement_disabled"
  else
    fail "helper_false_when_entitlement_disabled"
    print_body "$REST_BODY_FILE"
  fi
  if [[ "$ENTRY2A_RUN_DISABLED_TEST" != "1" ]]; then
    edge_request "reject_disabled_entitlement" "/outreach/enqueue" "enqueue_disabled"
    expect_edge "reject_disabled_entitlement" "403" "o.get('ok') is False and o.get('error') == 'account_outreach_entitlement_required'"
  fi
  rest_patch "/rest/v1/client_entitlements?id=eq.${ENTRY2A_ENTITLEMENT_ID}" "true" "restore_entitlement"
  expect_rest "restore_entitlement" "200,204"
else
  skip_rest "disable_entitlement"
  skip_rest "helper_false_when_entitlement_disabled"
  skip_rest "restore_entitlement"
fi

if [[ "${RUN_ENTRY1_REGRESSION:-0}" == "1" ]]; then
  echo
  echo "== Entry 1 regression script =="
  ./scripts/validate-entry1.sh && pass "validate_entry1_regression" || fail "validate_entry1_regression"
fi

echo
echo "Summary: PASS=$PASS_COUNT FAIL=$FAIL_COUNT"
if [[ "$FAIL_COUNT" -ne 0 ]]; then
  exit 1
fi
