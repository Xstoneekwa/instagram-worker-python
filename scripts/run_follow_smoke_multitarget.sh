#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run_follow_smoke_multitarget.sh <username> <follow_limit> <max_targets_per_run> <max_follows_per_target_per_run>

Examples:
  ./scripts/run_follow_smoke_multitarget.sh account_username 1 1 1
  ./scripts/run_follow_smoke_multitarget.sh account_username 2 2 1

Caps notation:
  follow_limit / max_targets_per_run / max_follows_per_target_per_run

Notes:
  - DB settings must match the requested caps exactly (ig_account_settings + follow source rotation).
  - Welcome, Outreach, and Unfollow must remain OFF for follow-only smokes.
  - At least max_targets_per_run eligible follow targets must exist.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

USERNAME="${1:-}"
FOLLOW_LIMIT="${2:-}"
MAX_TARGETS_PER_RUN="${3:-}"
MAX_FOLLOWS_PER_TARGET_PER_RUN="${4:-}"

if [[ -z "$USERNAME" || -z "$FOLLOW_LIMIT" || -z "$MAX_TARGETS_PER_RUN" || -z "$MAX_FOLLOWS_PER_TARGET_PER_RUN" ]]; then
  echo "STOP reason=usage"
  usage >&2
  exit 10
fi

if ! [[ "$FOLLOW_LIMIT" =~ ^[1-9][0-9]*$ && "$MAX_TARGETS_PER_RUN" =~ ^[1-9][0-9]*$ && "$MAX_FOLLOWS_PER_TARGET_PER_RUN" =~ ^[1-9][0-9]*$ ]]; then
  echo "STOP reason=invalid_caps"
  exit 10
fi

CAPS_TAG="${FOLLOW_LIMIT}x${MAX_TARGETS_PER_RUN}x${MAX_FOLLOWS_PER_TARGET_PER_RUN}"
SAFE_USERNAME_TAG="$(printf '%s' "$USERNAME" | tr -c 'A-Za-z0-9_' '_' | cut -c1-60)"
WORKER_ID="run-dispatcher:follow-mt-${SAFE_USERNAME_TAG}-${CAPS_TAG}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

"$ROOT_DIR/scripts/follow_smoke_local_preflight.sh" "$ROOT_DIR" "$ROOT_DIR/scripts/run_follow_smoke_multitarget.sh"
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
LOG_PATH="runs/follow_mt_${CAPS_TAG}_${USERNAME}_${TS}.log"
META_PATH="runs/follow_mt_${CAPS_TAG}_${USERNAME}_${TS}.meta.json"

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

PREFLIGHT_LOG="$(mktemp "${TMPDIR:-/tmp}/follow_smoke_mt_preflight.XXXXXX")"
REQUEST_LOG="$(mktemp "${TMPDIR:-/tmp}/follow_smoke_mt_request.XXXXXX")"
SUMMARY_LOG=""
cleanup_smoke_temps() {
  rm -f "$PREFLIGHT_LOG" "$REQUEST_LOG" "$SUMMARY_LOG"
}
trap cleanup_smoke_temps EXIT
set +e
python3 - "$USERNAME" "$META_PATH" "$FOLLOW_LIMIT" "$MAX_TARGETS_PER_RUN" "$MAX_FOLLOWS_PER_TARGET_PER_RUN" >"$PREFLIGHT_LOG" 2>&1 <<'PY'
from __future__ import annotations

import json
import sys

import supabase_client
from assignment_dispatch_resolver import resolve_account_assignment_runtime_context

USERNAME = sys.argv[1]
META_PATH = sys.argv[2]
REQUESTED_FOLLOW_LIMIT = int(sys.argv[3])
REQUESTED_MAX_TARGETS_PER_RUN = int(sys.argv[4])
REQUESTED_MAX_FOLLOWS_PER_TARGET_PER_RUN = int(sys.argv[5])


def stop(reason: str) -> None:
    print(f"STOP reason={reason}")
    raise SystemExit(10)


def rows(table: str, query: dict[str, str]) -> list[dict]:
    value = supabase_client._request_json("GET", table, query=query) or []
    return value if isinstance(value, list) else []


account_rows = rows(
    "ig_accounts",
    {"select": "id,username", "username": f"eq.{USERNAME}", "limit": "2"},
)
if len(account_rows) != 1:
    stop("account_not_found")
ACCOUNT_ID = str(account_rows[0].get("id") or "").strip()
if not ACCOUNT_ID:
    stop("account_id_missing")

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
        "select": "login_status,provisioning_status,onboarding_status",
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
if str(status.get("onboarding_status") or "") != "ready":
    stop("onboarding_status_not_ready")

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
    stop("assignment_not_found")
DEVICE_SERIAL = str(ctx.get("adb_serial") or "").strip()
PACKAGE_NAME = str(ctx.get("package_name") or "").strip()
if not DEVICE_SERIAL:
    stop("assignment_device_missing")
if not PACKAGE_NAME:
    stop("assignment_package_missing")

targets = supabase_client.load_eligible_follow_targets(
    ACCOUNT_ID,
    limit=REQUESTED_MAX_TARGETS_PER_RUN,
)
if len(targets) < REQUESTED_MAX_TARGETS_PER_RUN:
    stop("insufficient_eligible_follow_targets")

settings_rows = rows(
    "ig_account_settings",
    {
        "select": "follow_limit,app_package,follow_enabled,like_enabled,mute_posts_after_follow,mute_stories_after_follow,welcome_dm_enabled,cold_dm_enabled,unfollow_enabled",
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
if follow_limit != REQUESTED_FOLLOW_LIMIT:
    stop("account_caps_not_aligned")
if str(settings.get("app_package") or "") != PACKAGE_NAME:
    stop("unexpected_package")
if not bool(settings.get("follow_enabled")):
    stop("follow_disabled")
if not bool(settings.get("like_enabled")):
    stop("like_disabled")
if not bool(settings.get("mute_posts_after_follow")):
    stop("mute_posts_disabled")
if not bool(settings.get("mute_stories_after_follow")):
    stop("mute_stories_disabled")
if bool(settings.get("welcome_dm_enabled")):
    stop("welcome_enabled")
if bool(settings.get("cold_dm_enabled")):
    stop("outreach_enabled")
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
    stop("account_caps_not_aligned")
if int(rotation.get("max_targets_per_run") or 0) != REQUESTED_MAX_TARGETS_PER_RUN:
    stop("account_caps_not_aligned")
if int(rotation.get("max_follows_per_target_per_run") or 0) != REQUESTED_MAX_FOLLOWS_PER_TARGET_PER_RUN:
    stop("account_caps_not_aligned")

caps_tag = f"{REQUESTED_FOLLOW_LIMIT}x{REQUESTED_MAX_TARGETS_PER_RUN}x{REQUESTED_MAX_FOLLOWS_PER_TARGET_PER_RUN}"
with open(META_PATH, "w", encoding="utf-8") as fh:
    json.dump(
        {
            "account_id": ACCOUNT_ID,
            "username": USERNAME,
            "device_serial": DEVICE_SERIAL,
            "package_name": PACKAGE_NAME,
            "caps": {
                "follow_limit": REQUESTED_FOLLOW_LIMIT,
                "max_targets_per_run": REQUESTED_MAX_TARGETS_PER_RUN,
                "max_follows_per_target_per_run": REQUESTED_MAX_FOLLOWS_PER_TARGET_PER_RUN,
            },
        },
        fh,
        sort_keys=True,
    )

print(f"account_id={ACCOUNT_ID}")
print(f"device_serial={DEVICE_SERIAL}")
print(f"package_name={PACKAGE_NAME}")
print(
    "caps="
    f"follow_limit={REQUESTED_FOLLOW_LIMIT},"
    f"max_targets_per_run={REQUESTED_MAX_TARGETS_PER_RUN},"
    f"max_follows_per_target_per_run={REQUESTED_MAX_FOLLOWS_PER_TARGET_PER_RUN}"
)
PY
PREFLIGHT_EC=$?
set -e
if [[ "$PREFLIGHT_EC" -ne 0 ]]; then
  emit_python_gate_failure preflight "$PREFLIGHT_LOG"
  exit 10
fi
grep '^caps=' "$PREFLIGHT_LOG" || true

eval "$(
  python3 - "$META_PATH" <<'PY'
import json
import shlex
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    meta = json.load(fh)
for key, env_key in (
    ("account_id", "ACCOUNT_ID"),
    ("device_serial", "DEVICE_SERIAL"),
    ("package_name", "PACKAGE_NAME"),
):
    print(f"{env_key}={shlex.quote(str(meta.get(key) or ''))}")
PY
)"

if [[ -z "$ACCOUNT_ID" || -z "$DEVICE_SERIAL" || -z "$PACKAGE_NAME" ]]; then
  echo "STOP reason=assignment_not_found"
  exit 10
fi

if [[ "$(adb -s "$DEVICE_SERIAL" get-state 2>/dev/null || true)" != "device" ]]; then
  echo "STOP reason=device_offline"
  exit 10
fi

if ! adb -s "$DEVICE_SERIAL" shell pm path "$PACKAGE_NAME" >/dev/null 2>&1; then
  echo "STOP reason=unexpected_package"
  exit 10
fi

FAST_IME="$(python3 - <<'PY'
import config
print(str(getattr(config, "FAST_IME", "") or "").strip())
PY
)"
CURRENT_IME="$(adb -s "$DEVICE_SERIAL" shell settings get secure default_input_method 2>/dev/null | tr -d '\r' | tail -n 1)"
if [[ -z "$FAST_IME" || "$CURRENT_IME" != "$FAST_IME" ]]; then
  echo "STOP reason=adbkeyboard_not_active"
  exit 10
fi

set +e
python3 - "$USERNAME" "$META_PATH" "$FOLLOW_LIMIT" "$MAX_TARGETS_PER_RUN" "$MAX_FOLLOWS_PER_TARGET_PER_RUN" >"$REQUEST_LOG" 2>&1 <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from account_run_control import create_account_run_request

USERNAME = sys.argv[1]
META_PATH = sys.argv[2]
REQUESTED_FOLLOW_LIMIT = int(sys.argv[3])
REQUESTED_MAX_TARGETS_PER_RUN = int(sys.argv[4])
REQUESTED_MAX_FOLLOWS_PER_TARGET_PER_RUN = int(sys.argv[5])


def stop(reason: str) -> None:
    print(f"STOP reason={reason}")
    raise SystemExit(10)


with open(META_PATH, "r", encoding="utf-8") as fh:
    meta = json.load(fh)

account_id = str(meta.get("account_id") or "").strip()
package_name = str(meta.get("package_name") or "").strip()
device_serial = str(meta.get("device_serial") or "").strip()
if not (account_id and package_name and device_serial):
    stop("assignment_not_found")

caps_tag = f"{REQUESTED_FOLLOW_LIMIT}x{REQUESTED_MAX_TARGETS_PER_RUN}x{REQUESTED_MAX_FOLLOWS_PER_TARGET_PER_RUN}"
request = create_account_run_request(
    account_id=account_id,
    actor_type="admin",
    source_surface="fast_iteration_test",
    requested_run_type="account_session",
    idempotency_key=(
        f"follow-smoke-mt:{USERNAME}:{caps_tag}:"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    ),
    metadata_safe={
        "mode": "fast_iteration_test",
        "username": USERNAME,
        "expected_package": package_name,
        "expected_device_serial_suffix": device_serial[-4:],
        "caps": {
            "follow_limit": REQUESTED_FOLLOW_LIMIT,
            "max_targets_per_run": REQUESTED_MAX_TARGETS_PER_RUN,
            "max_follows_per_target_per_run": REQUESTED_MAX_FOLLOWS_PER_TARGET_PER_RUN,
        },
    },
)
request_id = str(request.get("id") or "")
if not request_id:
    stop("request_create_failed")

meta["request_id"] = request_id
with open(META_PATH, "w", encoding="utf-8") as fh:
    json.dump(meta, fh, sort_keys=True)

print(f"request_id={request_id}")
print(
    "caps="
    f"follow_limit={REQUESTED_FOLLOW_LIMIT},"
    f"max_targets_per_run={REQUESTED_MAX_TARGETS_PER_RUN},"
    f"max_follows_per_target_per_run={REQUESTED_MAX_FOLLOWS_PER_TARGET_PER_RUN}"
)
PY
REQUEST_EC=$?
set -e
if [[ "$REQUEST_EC" -ne 0 ]]; then
  emit_python_gate_failure request "$REQUEST_LOG"
  exit 10
fi
grep '^request_id=' "$REQUEST_LOG" || true
grep '^caps=' "$REQUEST_LOG" || true

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
export FOLLOW_MAX_PER_RUN="$FOLLOW_LIMIT"
export FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN="$MAX_TARGETS_PER_RUN"
export FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN="$MAX_FOLLOWS_PER_TARGET_PER_RUN"
export INSTAGRAM_PACKAGE="$PACKAGE_NAME"

set +e
python3 account_run_request_consumer.py --once >"$LOG_PATH" 2>&1
DISPATCH_EXIT=$?
set -e

SUMMARY_LOG="$(mktemp "${TMPDIR:-/tmp}/follow_smoke_mt_summary.XXXXXX")"
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
caps = meta.get("caps") if isinstance(meta.get("caps"), dict) else {}

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
print(
    "caps="
    f"follow_limit={caps.get('follow_limit', 'null')},"
    f"max_targets_per_run={caps.get('max_targets_per_run', 'null')},"
    f"max_follows_per_target_per_run={caps.get('max_follows_per_target_per_run', 'null')}"
)

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
