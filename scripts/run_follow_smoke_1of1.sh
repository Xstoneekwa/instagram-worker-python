#!/usr/bin/env bash
set -euo pipefail

USERNAME="${1:-i_m_your_traker}"
EXPECTED_USERNAME="i_m_your_traker"
ACCOUNT_ID="83de9cc9-5c37-42d1-9edc-c924352b17b1"
DEVICE_SERIAL="RFGL145VCKE"
PACKAGE_NAME="com.instagram.androie"
WORKER_ID="run-dispatcher:imytracker-follow-1of1"

if [[ "$USERNAME" != "$EXPECTED_USERNAME" ]]; then
  echo "STOP reason=unexpected_username"
  exit 10
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

"$ROOT_DIR/scripts/follow_smoke_local_preflight.sh" "$ROOT_DIR" "$ROOT_DIR/scripts/run_follow_smoke_1of1.sh"
echo "LOCAL_PREFLIGHT_OK"

ENV_FILE="${FOLLOW_SMOKE_ENV_FILE:-$ROOT_DIR/.env}"

load_env_file() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$file"
  set +a
  return 0
}

ENV_FILE_LOADED=false
if load_env_file "$ENV_FILE"; then
  ENV_FILE_LOADED=true
fi

if [[ -z "${SUPABASE_URL:-}" || -z "${SUPABASE_SERVICE_ROLE_KEY:-}" ]]; then
  if [[ "$ENV_FILE_LOADED" == true ]]; then
    echo "STOP reason=supabase_env_missing"
  else
    echo "STOP reason=env_not_loaded"
  fi
  exit 10
fi

mkdir -p runs
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_PATH="runs/follow_1of1_${USERNAME}_${TS}.log"
META_PATH="runs/follow_1of1_${USERNAME}_${TS}.meta.json"

emit_python_gate_failure() {
  local label="$1"
  local log_path="$2"
  local stop_line=""
  stop_line="$(grep -E '^STOP reason=' "$log_path" 2>/dev/null | tail -n 1 || true)"
  if [[ -n "$stop_line" ]]; then
    echo "$stop_line"
    return 10
  fi
  if grep -qE 'SUPABASE_URL is not set|SUPABASE_SERVICE_ROLE_KEY is not set' "$log_path" 2>/dev/null; then
    echo "STOP reason=supabase_env_missing"
    return 10
  fi
  echo "STOP reason=${label}_failed"
  return 10
}

if ! command -v adb >/dev/null 2>&1; then
  echo "STOP reason=adb_not_found"
  exit 10
fi

if [[ "$(adb -s "$DEVICE_SERIAL" get-state 2>/dev/null || true)" != "device" ]]; then
  echo "STOP reason=device_offline"
  exit 10
fi

if ! adb -s "$DEVICE_SERIAL" shell pm path "$PACKAGE_NAME" >/dev/null 2>&1; then
  echo "STOP reason=package_resolved_not_expected"
  exit 10
fi

PREFLIGHT_LOG="$(mktemp "${TMPDIR:-/tmp}/follow_smoke_preflight.XXXXXX")"
SUMMARY_LOG=""
cleanup_smoke_temps() {
  rm -f "$PREFLIGHT_LOG" "$SUMMARY_LOG"
}
trap cleanup_smoke_temps EXIT
set +e
python3 - "$USERNAME" "$META_PATH" >"$PREFLIGHT_LOG" 2>&1 <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import supabase_client
from account_run_control import create_account_run_request
from assignment_dispatch_resolver import resolve_account_assignment_runtime_context

USERNAME = sys.argv[1]
META_PATH = sys.argv[2]

ACCOUNT_ID = "83de9cc9-5c37-42d1-9edc-c924352b17b1"
EXPECTED_USERNAME = "i_m_your_traker"
DEVICE_SERIAL = "RFGL145VCKE"
PACKAGE_NAME = "com.instagram.androie"


def stop(reason: str) -> None:
    print(f"STOP reason={reason}")
    raise SystemExit(10)


def rows(table: str, query: dict[str, str]) -> list[dict]:
    value = supabase_client._request_json("GET", table, query=query) or []
    return value if isinstance(value, list) else []


account_rows = rows(
    "ig_accounts",
    {"select": "id,username", "id": f"eq.{ACCOUNT_ID}", "limit": "1"},
)
if not account_rows:
    stop("account_not_found")
if str(account_rows[0].get("username") or "") != EXPECTED_USERNAME or USERNAME != EXPECTED_USERNAME:
    stop("account_id_username_mismatch")

active_runs = rows(
    "ig_runs",
    {
        "select": "id,status",
        "account_id": f"eq.{ACCOUNT_ID}",
        "status": "in.(queued,pending,starting,running,in_progress,active)",
        "limit": "1",
    },
)
if active_runs:
    stop("active_ig_run_exists")

active_requests = rows(
    "account_run_requests",
    {
        "select": "id,status",
        "account_id": f"eq.{ACCOUNT_ID}",
        "status": "in.(queued,claimed,starting,running,in_progress)",
        "limit": "1",
    },
)
if active_requests:
    stop("active_account_run_request_exists")

live_views = rows(
    "live_view_sessions",
    {
        "select": "id,status",
        "account_id": f"eq.{ACCOUNT_ID}",
        "status": "in.(starting,active,running)",
        "limit": "1",
    },
)
if live_views:
    stop("active_live_view_session_exists")

status_rows = rows(
    "client_instagram_accounts",
    {
        "select": "login_status,provisioning_status",
        "account_id": f"eq.{ACCOUNT_ID}",
        "limit": "1",
    },
)
if not status_rows:
    stop("account_status_missing")
status = status_rows[0]
if str(status.get("login_status") or "") != "connected":
    stop("login_status_not_connected")
if str(status.get("provisioning_status") or "") != "ready":
    stop("provisioning_status_not_ready")

credential_rows = rows(
    "account_credentials",
    {
        "select": "id,status,reauth_required",
        "account_id": f"eq.{ACCOUNT_ID}",
        "provider": "eq.instagram",
        "status": "eq.active",
        "limit": "1",
    },
)
if not credential_rows:
    stop("credentials_not_active")
if bool(credential_rows[0].get("reauth_required")):
    stop("reauth_required")

ctx = resolve_account_assignment_runtime_context(
    ACCOUNT_ID,
    "account_session",
    require_assignment=True,
    enforce_window=True,
)
if ctx.get("reason") != "assignment_resolved":
    stop(str(ctx.get("reason") or "assignment_not_open"))
if str(ctx.get("adb_serial") or "") != DEVICE_SERIAL:
    stop("device_serial_mismatch")
if str(ctx.get("package_name") or "") != PACKAGE_NAME:
    stop("package_resolved_not_expected")

targets = supabase_client.load_eligible_follow_targets(ACCOUNT_ID, limit=1)
if len(targets) < 1:
    stop("no_eligible_follow_target")

settings_rows = rows(
    "ig_account_settings",
    {
        "select": "follow_limit,welcome_dm_enabled,unfollow_enabled",
        "account_id": f"eq.{ACCOUNT_ID}",
        "limit": "1",
    },
)
if not settings_rows:
    stop("account_settings_missing")
settings = settings_rows[0]
try:
    follow_limit = int(settings.get("follow_limit") or 0)
except (TypeError, ValueError):
    follow_limit = 0
if follow_limit != 1:
    stop("follow_limit_not_1")
if bool(settings.get("welcome_dm_enabled")):
    stop("welcome_enabled")
if bool(settings.get("unfollow_enabled")):
    stop("unfollow_enabled")

dm_rows = rows(
    "ig_account_dm_settings",
    {
        "select": "welcome_enabled,outreach_enabled",
        "account_id": f"eq.{ACCOUNT_ID}",
        "limit": "1",
    },
)
if dm_rows and bool(dm_rows[0].get("welcome_enabled")):
    stop("welcome_enabled")
if dm_rows and bool(dm_rows[0].get("outreach_enabled")):
    stop("outreach_enabled")

unfollow_rows = rows(
    "ig_account_unfollow_settings",
    {
        "select": "unfollow_enabled",
        "account_id": f"eq.{ACCOUNT_ID}",
        "limit": "1",
    },
)
if unfollow_rows and bool(unfollow_rows[0].get("unfollow_enabled")):
    stop("unfollow_enabled")

rotation = supabase_client.load_account_follow_source_settings(ACCOUNT_ID)
if not rotation:
    stop("follow_source_settings_missing")
if int(rotation.get("max_targets_per_run") or 0) != 1:
    stop("max_targets_per_run_not_1")
if int(rotation.get("max_follows_per_target_per_run") or 0) != 1:
    stop("max_follows_per_target_not_1")

request = create_account_run_request(
    account_id=ACCOUNT_ID,
    actor_type="admin",
    source_surface="fast_iteration_test",
    requested_run_type="account_session",
    idempotency_key=f"follow-smoke-1of1:{USERNAME}:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    metadata_safe={
        "mode": "fast_iteration_test",
        "username": USERNAME,
        "expected_package": PACKAGE_NAME,
        "expected_device_serial_suffix": DEVICE_SERIAL[-4:],
        "caps": {
            "follow_limit": 1,
            "max_targets_per_run": 1,
            "max_follows_per_target_per_run": 1,
        },
    },
)
request_id = str(request.get("id") or "")
if not request_id:
    stop("request_create_failed")

with open(META_PATH, "w", encoding="utf-8") as fh:
    json.dump({"request_id": request_id, "account_id": ACCOUNT_ID}, fh, sort_keys=True)

print(f"request_id={request_id}")
PY
PREFLIGHT_EC=$?
set -e
if [[ "$PREFLIGHT_EC" -ne 0 ]]; then
  emit_python_gate_failure preflight "$PREFLIGHT_LOG"
  exit 10
fi
grep '^request_id=' "$PREFLIGHT_LOG" || true

export RUN_CONTROL_DISPATCHER_ENABLED=true
export RUN_CONTROL_DISPATCHER_HEALTH_ONLY=false
export RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED=true
export RUN_CONTROL_DISPATCHER_WORKER_ID="$WORKER_ID"
export RUN_CONTROL_DISPATCHER_ALLOWED_RUN_TYPES=account_session
export RUN_CONTROL_DISPATCHER_TEST_ACCOUNT_IDS="$ACCOUNT_ID"
export RUN_CONTROL_DISPATCHER_REQUIRE_ASSIGNMENT=true
export RUN_CONTROL_DISPATCHER_ENFORCE_ASSIGNMENT_WINDOW=true
export RUN_CONTROL_DISPATCHER_POLL_SECONDS=1
export RUN_CONTROL_DISPATCHER_SUBPROCESS_TIMEOUT_SECONDS=900
export ACCOUNT_ASSIGNMENT_DISPATCH_ENABLED=true
export ACCOUNT_ASSIGNMENT_DISPATCH_REQUIRE_ASSIGNMENT=true
export ACCOUNT_ASSIGNMENT_DISPATCH_ENFORCE_WINDOW=true
export ACCOUNT_ASSIGNMENT_DISPATCH_RUN_TYPES=account_session
export FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN=1
export FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN=1
export INSTAGRAM_PACKAGE="$PACKAGE_NAME"

set +e
python3 account_run_request_consumer.py --once >"$LOG_PATH" 2>&1
DISPATCH_EXIT=$?
set -e

SUMMARY_LOG="$(mktemp "${TMPDIR:-/tmp}/follow_smoke_summary.XXXXXX")"
set +e
python3 - "$META_PATH" "$LOG_PATH" "$DISPATCH_EXIT" >"$SUMMARY_LOG" 2>&1 <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime

import supabase_client

META_PATH = sys.argv[1]
LOG_PATH = sys.argv[2]
DISPATCH_EXIT = int(sys.argv[3])


def rows(table: str, query: dict[str, str]) -> list[dict]:
    value = supabase_client._request_json("GET", table, query=query) or []
    return value if isinstance(value, list) else []


def parse_ts(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


with open(META_PATH, "r", encoding="utf-8") as fh:
    meta = json.load(fh)
request_id = str(meta.get("request_id") or "")

request_rows = rows(
    "account_run_requests",
    {
        "select": "id,status,run_id,created_at,completed_at,error_code",
        "id": f"eq.{request_id}",
        "limit": "1",
    },
)
request = request_rows[0] if request_rows else {}
run_id = str(request.get("run_id") or "")
run = {}
if run_id:
    run_rows = rows(
        "ig_runs",
        {
            "select": "id,status,started_at,completed_at,total_follow,total_like",
            "id": f"eq.{run_id}",
            "limit": "1",
        },
    )
    run = run_rows[0] if run_rows else {}

start = parse_ts(run.get("started_at") or request.get("created_at"))
end = parse_ts(run.get("completed_at") or request.get("completed_at"))
duration_s = round((end - start).total_seconds(), 1) if start and end else None

print("FOLLOW_SMOKE_DONE")
print(f"request_id={request_id or 'null'}")
print(f"run_id={run_id or 'null'}")
print(f"log_path={LOG_PATH}")
print(f"exit_code={DISPATCH_EXIT}")
print(f"status={request.get('status') or 'unknown'}")
print(f"duration_s={duration_s if duration_s is not None else 'null'}")

if DISPATCH_EXIT != 0:
    raise SystemExit(DISPATCH_EXIT)
PY
SUMMARY_EC=$?
set -e
if [[ "$SUMMARY_EC" -ne 0 ]]; then
  emit_python_gate_failure summary "$SUMMARY_LOG"
  exit 10
fi
cat "$SUMMARY_LOG"
if [[ "$DISPATCH_EXIT" -ne 0 ]]; then
  exit "$DISPATCH_EXIT"
fi
