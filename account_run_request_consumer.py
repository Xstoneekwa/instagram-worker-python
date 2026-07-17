"""Supervised Run Control dispatcher for account_run_requests.

RunControl-2: health-only mode (heartbeat, no claim/launch).
RunControl-4: claim + controlled subprocess launch when explicitly enabled.
"""

from __future__ import annotations

import contextlib
import os
import json
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import config
import runtime_heartbeat
from device import runner_subprocess_env
from account_run_control import (
    ACTIVE_REQUEST_STATUSES,
    claim_next_account_run_request,
    complete_account_run_request,
    get_account_run_request,
    insert_manual_run_audit,
    link_account_run_request_run,
    mark_account_run_request_starting,
    normalize_request_uuid,
    reconcile_linked_ig_run_terminal,
    reclaim_stale_account_run_requests,
)
from assignment_dispatch_resolver import resolve_account_assignment_runtime_context
from account_commercial_policy import evaluate_queued_run_commercial_policy
from account_commercial_policy import evaluate_queued_run_commercial_policy, sensitive_log_fields
from auto_restart_dispatcher_tick import run_auto_restart_dispatcher_tick, should_run_auto_restart_tick
from auto_restart_device_lock import acquire_device_lock, release_device_lock, release_device_lock_for_request, renew_device_lock, transfer_device_lock
from auto_restart_runtime import (
    is_auto_restart_request,
    is_hard_stop_reason,
    log_auto_restart_claim_validation,
    runner_env_for_resume_policy,
    validate_auto_restart_request_at_claim,
)
from logs import log
import incident_notifications
import runtime_incidents
import supabase_client
from runtime_incident_matrix import (
    build_run_failure_incident_payload,
    classify_terminal_run_failure,
)

LOGIN_RUN_TYPES = frozenset({"login_provisioning", "login_email_code_resume"})
ORPHAN_RECOVERY_RUN_TYPE = "login_orphan_challenge_recovery"
DEVICE_BOUND_RUN_TYPES = frozenset({
    "account_session",
    "outreach_session",
    "scheduled_session_preflight",
})
PREFLIGHT_RUN_TYPE = "scheduled_session_preflight"
_last_integration_noop_proof: dict[str, Any] | None = None


def _integration_mode_enabled() -> bool:
    return _env_bool("RUN_CONTROL_INTEGRATION_MODE", False)


def _integration_noop_runner_enabled() -> bool:
    return _integration_mode_enabled() and _env_bool("RUN_CONTROL_INTEGRATION_NOOP_RUNNER", False)


def _is_loopback_supabase_url(url: str) -> bool:
    lowered = str(url or "").strip().lower()
    return any(host in lowered for host in ("127.0.0.1", "localhost", "[::1]", "::1"))


def _assert_integration_safety() -> None:
    if not _integration_mode_enabled():
        return
    url = str(os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip()
    if not url or not _is_loopback_supabase_url(url):
        raise RuntimeError("integration mode requires loopback SUPABASE_URL")
    if _integration_noop_runner_enabled():
        for forbidden in ("ANDROID_SERIAL", "ADB_SERIAL", "U2_DEVICE"):
            if str(os.environ.get(forbidden) or "").strip():
                raise RuntimeError(f"integration noop forbids env {forbidden}")


def _is_device_bound_run_type(run_type: str) -> bool:
    return str(run_type or "").strip().lower() in DEVICE_BOUND_RUN_TYPES


def _pending_manual_lock_worker_id(request_id: str) -> str:
    return f"pending-request:{request_id}"


@dataclass
class DispatcherConfig:
    enabled: bool
    health_only: bool
    launch_enabled: bool
    worker_id: str
    poll_seconds: float
    lease_seconds: int
    heartbeat_seconds: float
    allowed_run_types: list[str]
    test_account_ids: set[str]
    subprocess_timeout_seconds: int
    require_assignment: bool
    enforce_assignment_window: bool
    max_consecutive_loop_errors: int = 10
    max_concurrent_subprocesses: int = 4


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(getattr(config, name, default))
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        try:
            return int(getattr(config, name, default))
        except (TypeError, ValueError):
            return int(default)
    try:
        return int(str(raw).strip())
    except ValueError:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        try:
            return float(getattr(config, name, default))
        except (TypeError, ValueError):
            return float(default)
    try:
        return float(str(raw).strip())
    except ValueError:
        return float(default)


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return str(getattr(config, name, default)).strip()
    value = str(raw).strip()
    return value if value else str(default)


def load_dispatcher_config() -> DispatcherConfig:
    host = socket.gethostname()
    default_worker_id = f"run-dispatcher:{host}"
    configured_worker_id = _env_str("RUN_CONTROL_DISPATCHER_WORKER_ID", default_worker_id)
    allowed_raw = _env_str(
        "RUN_CONTROL_DISPATCHER_ALLOWED_RUN_TYPES",
        "account_session,outreach_session,login_provisioning,login_email_code_resume,login_orphan_challenge_recovery,scheduled_session_preflight",
    )
    allowed = [part.strip().lower() for part in allowed_raw.split(",") if part.strip()]
    test_ids_raw = _env_str("RUN_CONTROL_DISPATCHER_TEST_ACCOUNT_IDS", "")
    test_ids = {part.strip() for part in test_ids_raw.split(",") if part.strip()}
    return DispatcherConfig(
        enabled=_env_bool("RUN_CONTROL_DISPATCHER_ENABLED", False),
        health_only=_env_bool("RUN_CONTROL_DISPATCHER_HEALTH_ONLY", True),
        launch_enabled=_env_bool("RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED", False),
        worker_id=configured_worker_id,
        poll_seconds=max(1.0, _env_float("RUN_CONTROL_DISPATCHER_POLL_SECONDS", 5.0)),
        lease_seconds=max(30, _env_int("RUN_CONTROL_DISPATCHER_LEASE_SECONDS", 120)),
        heartbeat_seconds=max(5.0, _env_float("RUN_CONTROL_DISPATCHER_HEARTBEAT_SECONDS", 20.0)),
        allowed_run_types=allowed,
        test_account_ids=test_ids,
        subprocess_timeout_seconds=max(60, _env_int("RUN_CONTROL_DISPATCHER_SUBPROCESS_TIMEOUT_SECONDS", 7200)),
        require_assignment=_env_bool("RUN_CONTROL_DISPATCHER_REQUIRE_ASSIGNMENT", False),
        enforce_assignment_window=_env_bool("RUN_CONTROL_DISPATCHER_ENFORCE_ASSIGNMENT_WINDOW", False),
        max_consecutive_loop_errors=max(1, _env_int("RUN_CONTROL_DISPATCHER_MAX_CONSECUTIVE_LOOP_ERRORS", 5)),
        max_concurrent_subprocesses=max(
            1,
            _env_int("RUN_CONTROL_DISPATCHER_MAX_CONCURRENT_SUBPROCESSES", 4),
        ),
    )


def dispatcher_is_healthy(cfg: DispatcherConfig | None = None) -> bool:
    """Return True when dispatcher is enabled and heartbeat is fresh."""
    cfg = cfg or load_dispatcher_config()
    if not cfg.enabled:
        return False
    try:
        rows = supabase_client._request_json(
            "GET",
            "worker_heartbeats",
            query={
                "select": "worker_id,status,last_seen_at",
                "worker_id": f"eq.{cfg.worker_id}",
                "limit": "1",
            },
        ) or []
    except Exception:
        return False
    if not rows:
        return False
    row = rows[0]
    status = str(row.get("status") or "").strip().lower()
    if status not in {"starting", "idle", "running"}:
        return False
    last_seen_raw = str(row.get("last_seen_at") or "").strip()
    if not last_seen_raw:
        return False
    try:
        last_seen = datetime.fromisoformat(last_seen_raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - last_seen.astimezone(timezone.utc)).total_seconds()
    return age_seconds <= max(cfg.heartbeat_seconds * 3, 60.0)


def _allow_existing_queue_on_startup() -> bool:
    return _env_bool("RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE", False)


def count_active_account_run_requests() -> int:
    """Read-only count of active queue rows for safe startup preflight."""
    try:
        rows = supabase_client._request_json(
            "GET",
            "account_run_requests",
            query={
                "select": "id",
                "status": f"in.({','.join(sorted(ACTIVE_REQUEST_STATUSES))})",
            },
        ) or []
    except Exception:
        return -1
    return len(rows)


def _classify_queue_read_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, supabase_client.SupabaseRestError):
        return str(exc.reason or "active_queue_read_failed"), str(exc)[:200]
    err = str(exc)
    lowered = err.lower()
    if "timed out" in lowered:
        return "supabase_rest_timeout", err[:200]
    if "401" in err:
        return "supabase_auth_401", err[:200]
    if "403" in err:
        return "supabase_auth_403", err[:200]
    if "dns" in lowered or "name or service not known" in lowered:
        return "supabase_dns_failed", err[:200]
    if "ssl" in lowered or "tls" in lowered:
        return "supabase_tls_failed", err[:200]
    return "active_queue_read_failed", err[:200]


def summarize_active_account_run_requests(limit: int = 20) -> dict[str, Any]:
    """Safe startup summary without secrets or device identifiers."""
    try:
        rows = supabase_client._request_json(
            "GET",
            "account_run_requests",
            query={
                "select": "id,account_id,status,requested_run_type,created_at",
                "status": f"in.({','.join(sorted(ACTIVE_REQUEST_STATUSES))})",
                "order": "created_at.asc",
                "limit": str(max(1, int(limit))),
            },
        ) or []
    except Exception as exc:
        reason, err = _classify_queue_read_failure(exc)
        return {
            "active_count": -1,
            "requests": [],
            "read_failed": True,
            "error": err,
            "supabase_reason": reason,
        }
    safe_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        safe_rows.append(
            {
                "request_id": normalize_request_uuid(row.get("id")),
                "account_id": normalize_request_uuid(row.get("account_id")),
                "status": str(row.get("status") or "").strip().lower() or None,
                "requested_run_type": str(row.get("requested_run_type") or "").strip().lower() or None,
                "created_at": str(row.get("created_at") or "").strip() or None,
            }
        )
    return {
        "active_count": len(safe_rows),
        "requests": safe_rows,
        "read_failed": False,
        "error": None,
    }


def evaluate_launch_mode_startup_preflight(cfg: DispatcherConfig) -> dict[str, Any]:
    """Block launch mode when an active queue exists unless explicitly overridden."""
    if cfg.health_only or not cfg.launch_enabled:
        return {
            "ok": True,
            "mode": "health_only",
            "active_count": 0,
            "allow_existing_queue": _allow_existing_queue_on_startup(),
        }

    summary = summarize_active_account_run_requests()
    active_count = int(summary.get("active_count") or 0)
    if summary.get("read_failed"):
        return {
            "ok": False,
            "reason": str(summary.get("supabase_reason") or "active_queue_read_failed"),
            "active_count": active_count,
            "allow_existing_queue": _allow_existing_queue_on_startup(),
            "requests": [],
            "error": summary.get("error"),
        }

    if active_count > 0 and not _allow_existing_queue_on_startup():
        return {
            "ok": False,
            "reason": "active_queue_present",
            "active_count": active_count,
            "allow_existing_queue": False,
            "requests": list(summary.get("requests") or []),
        }

    return {
        "ok": True,
        "reason": "ready",
        "active_count": active_count,
        "allow_existing_queue": _allow_existing_queue_on_startup(),
        "requests": list(summary.get("requests") or []),
    }


def _heartbeat(cfg: DispatcherConfig, *, status: str = "idle", metadata: dict[str, Any] | None = None) -> None:
    with _force_runtime_heartbeats():
        runtime_heartbeat.heartbeat_worker(
            worker_id=cfg.worker_id,
            status=status,
            metadata={
                "component": "run_control_dispatcher",
                "health_only": cfg.health_only,
                "launch_enabled": cfg.launch_enabled,
                **(metadata or {}),
            },
            force=True,
        )


class _force_runtime_heartbeats:
    def __enter__(self) -> None:
        self._previous = bool(getattr(config, "RUNTIME_HEARTBEATS_ENABLED", False))
        config.RUNTIME_HEARTBEATS_ENABLED = True

    def __exit__(self, exc_type, exc, tb) -> None:
        config.RUNTIME_HEARTBEATS_ENABLED = self._previous


def _account_is_launch_allowed(account_id: str, cfg: DispatcherConfig) -> tuple[bool, str | None]:
    if cfg.test_account_ids and account_id not in cfg.test_account_ids:
        return False, "test_account_only"
    account = supabase_client.load_account(account_id=account_id)
    if not account:
        return False, "account_not_found"
    lifecycle = str(account.get("status") or account.get("lifecycle_status") or "active").strip().lower()
    if lifecycle in {"archived", "trashed", "canceled", "stopped", "deleted"}:
        return False, "account_not_active"
    return True, None


def _validate_assignment(account_id: str, run_type: str, cfg: DispatcherConfig) -> tuple[bool, str | None, dict[str, Any]]:
    ctx = resolve_account_assignment_runtime_context(
        account_id,
        run_type,
        require_assignment=cfg.require_assignment,
        enforce_window=cfg.enforce_assignment_window,
    )
    reason = str(ctx.get("reason") or "")
    if reason == "assignment_device_missing_adb_serial":
        if _integration_noop_runner_enabled():
            ctx = dict(ctx)
            ctx["adb_serial"] = "integration-noop"
            return True, None, ctx
        return False, reason, ctx
    if cfg.require_assignment and not bool(ctx.get("assignment_found")):
        reason = str(ctx.get("reason") or "assignment_not_found")
        return False, reason, ctx
    return True, None, ctx


def _is_login_run_type(run_type: str) -> bool:
    return str(run_type or "").strip().lower() in LOGIN_RUN_TYPES


def _is_orphan_recovery_run_type(run_type: str) -> bool:
    return str(run_type or "").strip().lower() == ORPHAN_RECOVERY_RUN_TYPE


def _is_scheduled_session_preflight_run_type(run_type: str) -> bool:
    return str(run_type or "").strip().lower() == PREFLIGHT_RUN_TYPE


def _build_scheduled_session_preflight_command(
    account_id: str,
    request_id: str,
    *,
    device_serial: str | None = None,
    package_name: str | None = None,
    expected_username: str | None = None,
    metadata_safe: dict[str, Any] | None = None,
) -> list[str]:
    meta = dict(metadata_safe or {})
    username = str(expected_username or meta.get("expected_username") or "").strip()
    if not username:
        username = _load_expected_username(account_id)
    runner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scheduled_session_preflight_runner.py")
    return [
        sys.executable,
        runner_path,
        "--account-id",
        account_id,
        "--request-id",
        request_id,
        "--device-serial",
        str(device_serial or "").strip(),
        "--package-name",
        str(package_name or "").strip(),
        "--expected-username",
        username,
        "--preflight-id",
        str(meta.get("preflight_id") or "").strip(),
        "--metadata-json",
        json.dumps(meta, separators=(",", ":"), sort_keys=True),
    ]


def _load_expected_username(account_id: str) -> str:
    account = supabase_client.load_account(account_id=account_id)
    if not account:
        return ""
    return str(account.get("username") or "").strip()


def _build_login_provisioner_command(
    account_id: str,
    run_type: str,
    request_id: str,
    *,
    device_serial: str | None = None,
    package_name: str | None = None,
    app_instance_id: str | None = None,
    metadata_safe: dict[str, Any] | None = None,
) -> list[str]:
    expected_username = _load_expected_username(account_id)
    cmd = [
        sys.executable,
        "-m",
        "instagram_login_provisioner_cli",
        "--account-id",
        account_id,
        "--expected-username",
        expected_username or "unknown",
        "--publish",
        "--json",
        "--run-id",
        request_id,
    ]
    serial = str(device_serial or "").strip()
    if serial:
        cmd.extend(["--device-serial", serial])
    package = str(package_name or "").strip()
    if package:
        cmd.extend(["--package-name", package])
    app_instance = str(app_instance_id or "").strip()
    if app_instance:
        cmd.extend(["--expected-app-instance-id", app_instance])
    meta = dict(metadata_safe or {})
    if str(run_type or "").strip().lower() == "login_email_code_resume":
        cmd.append("--resume-email-code-from-action")
        action_id = str(meta.get("action_id") or meta.get("verification_action_id") or "").strip()
        if action_id:
            cmd.extend(["--verification-action-id", action_id])
    return cmd


def _build_orphan_recovery_command(
    account_id: str,
    request_id: str,
    *,
    device_serial: str | None = None,
    package_name: str | None = None,
    app_instance_id: str | None = None,
    assignment_id: str | None = None,
    credentials_version: int | None = None,
    assignment_updated_at: str | None = None,
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "login_orphan_challenge_recovery_cli",
        "--account-id",
        account_id,
        "--run-id",
        request_id,
        "--json",
    ]
    serial = str(device_serial or "").strip()
    if serial:
        cmd.extend(["--device-serial", serial])
    package = str(package_name or "").strip()
    if package:
        cmd.extend(["--package-name", package])
    app_instance = str(app_instance_id or "").strip()
    if app_instance:
        cmd.extend(["--expected-app-instance-id", app_instance])
    assignment = str(assignment_id or "").strip()
    if assignment:
        cmd.extend(["--assignment-id", assignment])
    if credentials_version is not None:
        cmd.extend(["--credentials-version", str(int(credentials_version))])
    updated_at = str(assignment_updated_at or "").strip()
    if updated_at:
        cmd.extend(["--assignment-updated-at", updated_at])
    return cmd


def _build_runner_command(
    account_id: str,
    run_type: str,
    request_id: str,
    *,
    device_serial: str | None = None,
    package_name: str | None = None,
    app_instance_id: str | None = None,
    metadata_safe: dict[str, Any] | None = None,
) -> list[str]:
    if _integration_noop_runner_enabled():
        if _is_login_run_type(run_type) or _is_orphan_recovery_run_type(run_type):
            raise RuntimeError("integration noop runner cannot execute login flows")
        noop_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_restart_integration_noop_runner.py")
        return [
            sys.executable,
            noop_path,
            "--request-id",
            request_id,
            "--account-id",
            account_id,
        ]
    if _is_login_run_type(run_type):
        return _build_login_provisioner_command(
            account_id,
            run_type,
            request_id,
            device_serial=device_serial,
            package_name=package_name,
            app_instance_id=app_instance_id,
            metadata_safe=metadata_safe,
        )
    if _is_scheduled_session_preflight_run_type(run_type):
        return _build_scheduled_session_preflight_command(
            account_id,
            request_id,
            device_serial=device_serial,
            package_name=package_name,
            metadata_safe=metadata_safe,
        )
    if _is_orphan_recovery_run_type(run_type):
        meta = dict(metadata_safe or {})
        credentials_version = meta.get("credentials_version")
        return _build_orphan_recovery_command(
            account_id,
            request_id,
            device_serial=device_serial,
            package_name=package_name,
            app_instance_id=app_instance_id,
            assignment_id=str(meta.get("assignment_id") or ""),
            credentials_version=int(credentials_version) if credentials_version is not None else None,
            assignment_updated_at=str(meta.get("assignment_updated_at") or ""),
        )
    runner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner.py")
    cmd = [
        sys.executable,
        runner_path,
        "--account-id",
        account_id,
        "--run-type",
        run_type,
        "--run-request-id",
        request_id,
    ]
    serial = str(device_serial or "").strip()
    if serial:
        cmd.extend(["--device-serial", serial])
    package = str(package_name or "").strip()
    if package:
        cmd.extend(["--package-name", package])
    app_instance = str(app_instance_id or "").strip()
    if app_instance:
        cmd.extend(["--expected-app-instance-id", app_instance])
    return cmd


def _login_provisioner_env() -> dict[str, str]:
    env = runner_subprocess_env()
    env["LOGIN_PROVISIONER_PUBLISH_ENABLED"] = "true"
    return env


def _create_and_link_login_run(account_id: str, request_id: str, worker_id: str) -> str | None:
    try:
        run = supabase_client.create_run(account_id=account_id)
        run_id = str(run.get("id") or "").strip()
        if not run_id:
            return None
        linked = link_account_run_request_run(request_id, worker_id, run_id)
        if not linked:
            return None
        _audit(
            account_id=account_id,
            action_type="manual_run_started",
            status="success",
            message="Manual login run linked to ig_runs.",
            run_id=run_id,
            payload={"request_id": request_id, "run_type": "login_provisioning"},
        )
        return run_id
    except Exception as exc:
        log(
            "warning",
            "login_run_link_create_failed",
            account_id=account_id,
            request_id=request_id,
            error=str(exc)[:200],
        )
        return None


def _safe_login_provisioner_summary_for_audit(run_id: str | None) -> dict[str, Any]:
    safe_run_id = str(run_id or "").strip()
    if not safe_run_id:
        return {}
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "instagram_login_provisioner.jsonl")
    try:
        with open(log_path, "r", encoding="utf-8") as handle:
            matches = [
                json.loads(line)
                for line in handle
                if line.strip() and f'"run_id":"{safe_run_id}"' in line
            ]
    except Exception as exc:
        log("warning", "login_provisioner_summary_read_failed", run_id=safe_run_id, error=str(exc)[:200])
        return {}
    if not matches:
        return {}
    summary = dict(matches[-1])
    allowed_keys = {
        "run_id",
        "ok",
        "completed",
        "final_outcome",
        "reason",
        "failure_reason",
        "status_candidate",
        "published",
        "publish_enabled",
        "publish_result",
        "publish_reason",
        "publish_error_code",
        "app_start_attempted",
        "app_start_ok",
        "package_name",
        "expected_package_name",
        "actual_foreground_package",
        "package_guard_mismatch",
        "package_guard_checked",
        "transient_foreground_recovery_attempted",
        "transient_foreground_recovery_succeeded",
        "transient_foreground_packages_seen",
        "screen_after_app_start",
        "screen_after_app_start_initial",
        "screen_after_app_start_final",
        "screen_type",
        "screen_before_submit",
        "selected_route",
        "selected_route_reason",
        "final_terminal_screen",
        "post_submit_screens",
        "submit_executed",
        "would_submit_password",
        "login_form_empty_detected",
        "login_form_after_join_landing_detected",
        "credentials_error_code",
        "credentials_stage",
        "credentials_invalid_reason",
        "credential_metadata_found",
        "secret_loaded",
        "username_input_result",
        "password_input_result",
        "username_field_focused_before_input",
        "password_field_focused_before_input",
        "warnings",
        "dashboard_action_type",
        "final_login_status",
        "final_provisioning_status",
        "final_onboarding_status",
        "email_code_challenge_detected",
        "challenge_type",
    }
    return {key: summary.get(key) for key in allowed_keys if key in summary}


LOGIN_VERIFICATION_PAUSE_OUTCOMES = frozenset(
    {
        "verification_pending",
        "needs_2fa",
        "checkpoint",
        "unsupported_post_submit_challenge",
    }
)

LOGIN_VERIFICATION_PAUSE_ACTIONS = frozenset(
    {
        "enter_email_verification_code",
        "complete_two_factor",
        "resolve_checkpoint",
        "review_login_challenge",
    }
)

LOGIN_PROVISIONING_BLOCKED_REASONS = frozenset(
    {
        "orphan_challenge_provenance_weak",
        "pre_input_challenge_orphan",
        "historical_provenance_partial",
        "historical_app_instance_mismatch",
        "historical_assignment_mismatch",
        "historical_run_mismatch",
        "historical_challenge_expired",
        "assignment_changed_since_challenge",
    }
)

CLIENT_SAFE_ORPHAN_CHALLENGE_MESSAGE = (
    "Sign-in requires a security verification before it can continue."
)


def _is_login_verification_pause_summary(summary: dict[str, Any]) -> bool:
    if not summary:
        return False
    final_outcome = str(summary.get("final_outcome") or "").strip().lower()
    if final_outcome in LOGIN_VERIFICATION_PAUSE_OUTCOMES:
        return True
    dashboard_action_type = str(summary.get("dashboard_action_type") or "").strip()
    return dashboard_action_type in LOGIN_VERIFICATION_PAUSE_ACTIONS


def _is_login_provisioning_blocked_summary(summary: dict[str, Any]) -> bool:
    if not summary:
        return False
    if str(summary.get("final_outcome") or "").strip().lower() != "blocked":
        return False
    reason = str(summary.get("reason") or summary.get("failure_reason") or "").strip().lower()
    return reason in LOGIN_PROVISIONING_BLOCKED_REASONS or reason.startswith("orphan_challenge")


def _safe_complete_account_run_request(
    request_id: str | None,
    worker_id: str,
    status: str,
    *,
    error_code: str | None = None,
    error_message_safe: str | None = None,
) -> dict[str, Any] | None:
    normalized_request_id = normalize_request_uuid(request_id)
    if not normalized_request_id:
        log(
            "warning",
            "run_control_skip_transition_invalid_request_id",
            transition="complete",
            terminal_status=status,
            worker_id=worker_id,
        )
        return None
    return complete_account_run_request(
        normalized_request_id,
        worker_id,
        status,
        error_code=error_code,
        error_message_safe=error_message_safe,
    )


def _audit(
    *,
    account_id: str,
    action_type: str,
    status: str,
    message: str,
    run_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    try:
        insert_manual_run_audit(
            account_id=account_id,
            action_type=action_type,
            status=status,
            message=message,
            run_id=run_id,
            payload=payload or {},
        )
    except Exception as exc:
        log("warning", "manual_run_audit_failed", action_type=action_type, error=str(exc)[:200])


def _reconcile_linked_run(
    *,
    account_id: str,
    run_id: str | None,
    terminal_status: str,
    request_id: str,
    exit_code: int | None = None,
) -> dict[str, Any]:
    result = reconcile_linked_ig_run_terminal(
        run_id=run_id,
        terminal_status=terminal_status,
        account_id=account_id,
    )
    if result.get("reconciled"):
        payload: dict[str, Any] = {
            "request_id": request_id,
            "previous_status": result.get("previous_status"),
            "terminal_status": result.get("terminal_status"),
        }
        if exit_code is not None:
            payload["exit_code"] = exit_code
        _audit(
            account_id=account_id,
            action_type="orphan_running_run_reconciled",
            status="success",
            message="Linked ig_runs row reconciled after subprocess exit.",
            run_id=str(result.get("run_id") or run_id or "") or None,
            payload=payload,
        )
        log(
            "info",
            "orphan_running_run_reconciled",
            account_id=account_id,
            request_id=request_id,
            run_id=result.get("run_id"),
            previous_status=result.get("previous_status"),
            terminal_status=result.get("terminal_status"),
        )
    return result


def _upsert_operator_review_action(
    *,
    decision: IncidentDecision,
    incident_id: str,
    account_id: str,
    request_id: str,
    run_id: str | None,
) -> dict[str, Any]:
    if not decision.requires_operator_review:
        return {}
    return supabase_client.call_rpc(
        "upsert_account_dashboard_action",
        {
            "p_account_id": account_id,
            "p_client_id": None,
            "p_incident_id": incident_id,
            "p_action_type": "operator_review_required",
            "p_status": "pending_verification",
            "p_title": decision.operator_label or "Runtime failure requires operator review",
            "p_dedupe_key": f"account:{account_id}:run:{run_id or request_id}:dashboard_action:operator_review_required",
            "p_safe_client_message": None,
            "p_admin_message": decision.admin_message or decision.action_required,
            "p_assistant_message": decision.action_required or "Human review is required before the next launch.",
            "p_action_label": "Mark reviewed",
            "p_action_deep_link": "/instagram-dashboard/incidents",
            "p_severity": decision.severity,
            "p_audience": "admin",
            "p_requires_client_action": False,
            "p_blocking_campaign": decision.blocking_campaign,
            "p_metadata": {
                "source": "run_dispatcher",
                "request_id": request_id,
                "run_id": run_id,
                "reason": decision.reason_code,
                "incident_type": decision.incident_type,
                "review_workflow": "canonical_operator_review",
            },
        },
    )


def _publish_run_failure_incident(
    *,
    request_id: str,
    account_id: str,
    run_id: str | None,
    run_type: str | None,
    exit_code: int,
    timed_out: bool,
    canceled: bool,
) -> None:
    try:
        run_status: str | None = None
        performance_summary: dict[str, Any] | None = None
        if run_id:
            try:
                run_row = supabase_client.load_run_row(run_id) or {}
                run_status = str(run_row.get("status") or "").strip() or None
                raw_summary = run_row.get("performance_summary")
                if isinstance(raw_summary, dict):
                    performance_summary = raw_summary
            except Exception as exc:
                log(
                    "warning",
                    "run_incident_summary_load_failed",
                    account_id=account_id,
                    request_id=request_id,
                    run_id=run_id,
                    error=str(exc)[:300],
                )
        decision = classify_terminal_run_failure(
            exit_code=exit_code,
            timed_out=timed_out,
            run_status=run_status,
            canceled=canceled,
            performance_summary=performance_summary,
        )
        log(
            "info",
            "run_incident_classified",
            account_id=account_id,
            request_id=request_id,
            run_id=run_id,
            run_type=run_type or None,
            exit_code=exit_code,
            timed_out=timed_out,
            **decision.to_log_fields(),
        )
        if not decision.should_publish:
            return
        try:
            account_username = supabase_client.get_account_username(account_id) or None
        except Exception:
            account_username = None
        payload = build_run_failure_incident_payload(
            decision,
            account_id=account_id,
            account_username=account_username,
            run_id=run_id,
            run_request_id=request_id,
            run_type=run_type,
        )
        result = runtime_incidents.publish_account_incident(**payload)
        incident_id = str(result.get("incident_id") or "").strip()
        action_id = None
        if incident_id and decision.requires_operator_review:
            action = _upsert_operator_review_action(
                decision=decision,
                incident_id=incident_id,
                account_id=account_id,
                request_id=request_id,
                run_id=run_id,
            )
            action_id = action.get("id") if isinstance(action, dict) else None
            if action_id:
                _audit(
                    account_id=account_id,
                    action_type="operator_review_action_created",
                    status="success",
                    message="Canonical operator review action created for a runtime incident.",
                    run_id=run_id,
                    payload={
                        "incident_id": incident_id,
                        "dashboard_action_id": str(action_id),
                        "request_id": request_id,
                        "incident_type": decision.incident_type,
                        "reason": decision.reason_code,
                        "blocking_campaign": decision.blocking_campaign,
                    },
                )
                incident_notifications.dispatch_operator_review_action_notification(
                    event="created",
                    action_id=str(action_id),
                    incident_id=incident_id,
                    account_id=account_id,
                    account_username=account_username or "unknown",
                    reason=decision.admin_message or decision.reason_code,
                    final_status="pending_verification",
                    operator_id="system",
                )
        log(
            "info",
            "run_incident_publish_result",
            account_id=account_id,
            request_id=request_id,
            run_id=run_id,
            incident_type=decision.incident_type,
            reason_code=decision.reason_code,
            published=bool(result.get("published")),
            publish_reason=result.get("reason"),
            incident_id=incident_id or None,
            dashboard_action_id=action_id,
            occurrence_count=result.get("occurrence_count"),
        )
    except Exception as exc:
        log(
            "warning",
            "run_incident_publish_unexpected_error",
            account_id=account_id,
            request_id=request_id,
            run_id=run_id,
            error=str(exc)[:300],
        )


def _finalize_manual_run_after_subprocess(
    cfg: DispatcherConfig,
    *,
    request_id: str,
    account_id: str,
    exit_code: int,
    timed_out: bool = False,
    request_snapshot: dict[str, Any] | None = None,
) -> None:
    latest = get_account_run_request(request_id) or request_snapshot or {}
    run_id = str(latest.get("run_id") or "").strip() or None
    run_type = str(
        latest.get("requested_run_type")
        or (request_snapshot or {}).get("requested_run_type")
        or ""
    ).strip().lower()
    canceled = bool(latest.get("cancel_requested_at")) or str(latest.get("status") or "").strip().lower() == "canceled"

    if timed_out:
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "failed",
            error_code="subprocess_timeout",
            error_message_safe="Worker subprocess exceeded dispatcher timeout.",
        )
        _reconcile_linked_run(
            account_id=account_id,
            run_id=run_id,
            terminal_status="failed",
            request_id=request_id,
            exit_code=exit_code,
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_failed",
            status="failed",
            message="Worker subprocess exceeded dispatcher timeout.",
            run_id=run_id,
            payload={"request_id": request_id},
        )
        _publish_run_failure_incident(
            request_id=request_id,
            account_id=account_id,
            run_id=run_id,
            run_type=run_type,
            exit_code=exit_code,
            timed_out=True,
            canceled=canceled,
        )
        return

    if exit_code == 0:
        if _is_orphan_recovery_run_type(run_type):
            _safe_complete_account_run_request(
                request_id,
                cfg.worker_id,
                "completed",
                error_code="login_surface_restored",
                error_message_safe="Login surface restored.",
            )
            _audit(
                account_id=account_id,
                action_type="login_orphan_recovery_completed",
                status="success",
                message="Orphan login challenge recovery restored login surface.",
                run_id=run_id,
                payload={"request_id": request_id, "exit_code": exit_code, "recovery_state": "login_surface_restored"},
            )
            return
        if run_type == "account_session":
            run_row = supabase_client.load_run_row(run_id or "") or {}
            performance_summary = run_row.get("performance_summary")
            contract = (
                performance_summary.get("phase_terminal_contract")
                if isinstance(performance_summary, dict)
                else None
            )
            if not isinstance(contract, dict) or contract.get("ok") is not True:
                reason = "account_session_phase_not_terminal"
                _safe_complete_account_run_request(
                    request_id,
                    cfg.worker_id,
                    "failed",
                    error_code=reason,
                    error_message_safe="Account session stopped before every planned phase reached a terminal state.",
                )
                _reconcile_linked_run(
                    account_id=account_id,
                    run_id=run_id,
                    terminal_status="failed",
                    request_id=request_id,
                    exit_code=1,
                )
                _audit(
                    account_id=account_id,
                    action_type="account_session_terminal_contract_failed",
                    status="failed",
                    message="Account session terminal contract was not satisfied.",
                    run_id=run_id,
                    payload={"request_id": request_id, "phase_terminal_contract": contract},
                )
                _publish_run_failure_incident(
                    request_id=request_id,
                    account_id=account_id,
                    run_id=run_id,
                    run_type=run_type,
                    exit_code=1,
                    timed_out=False,
                    canceled=False,
                )
                return
        _safe_complete_account_run_request(request_id, cfg.worker_id, "completed")
        _reconcile_linked_run(
            account_id=account_id,
            run_id=run_id,
            terminal_status="completed",
            request_id=request_id,
            exit_code=exit_code,
        )
        summary = _safe_login_provisioner_summary_for_audit(run_id or request_id)
        _audit(
            account_id=account_id,
            action_type="manual_run_completed",
            status="success",
            message="Manual run completed.",
            run_id=run_id,
            payload={
                "request_id": request_id,
                "exit_code": exit_code,
                **({"login_provisioner_summary": summary} if summary else {}),
            },
        )
        return

    if canceled:
        _safe_complete_account_run_request(request_id, cfg.worker_id, "canceled")
        _reconcile_linked_run(
            account_id=account_id,
            run_id=run_id,
            terminal_status="canceled",
            request_id=request_id,
            exit_code=exit_code,
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_canceled",
            status="success",
            message="Manual run canceled.",
            run_id=run_id,
            payload={"request_id": request_id, "exit_code": exit_code},
        )
        return

    summary = _safe_login_provisioner_summary_for_audit(run_id or request_id)
    if _is_orphan_recovery_run_type(run_type):
        from login_orphan_recovery_state import resolve_orphan_recovery_state

        recovery_state = resolve_orphan_recovery_state(account_id)
        terminal_status = "blocked" if recovery_state.get("state") != "login_surface_restored" else "completed"
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            terminal_status,
            error_code=str(recovery_state.get("state") or "recovery_blocked"),
            error_message_safe="Orphan login challenge recovery stopped without restoring login surface.",
        )
        _audit(
            account_id=account_id,
            action_type="login_orphan_recovery_blocked",
            status="blocked",
            message="Orphan login challenge recovery blocked.",
            run_id=run_id,
            payload={
                "request_id": request_id,
                "exit_code": exit_code,
                "recovery_state": recovery_state,
            },
        )
        return
    if _is_login_verification_pause_summary(summary):
        _audit(
            account_id=account_id,
            action_type="manual_run_verification_paused",
            status="paused",
            message="Login provisioning paused awaiting client verification.",
            run_id=run_id,
            payload={
                "request_id": request_id,
                "exit_code": exit_code,
                **({"login_provisioner_summary": summary} if summary else {}),
            },
        )
        log(
            "info",
            "manual_run_verification_paused",
            account_id=account_id,
            request_id=request_id,
            run_id=run_id,
            worker_id=cfg.worker_id,
            exit_code=exit_code,
            final_outcome=str(summary.get("final_outcome") or ""),
            dashboard_action_type=str(summary.get("dashboard_action_type") or ""),
        )
        return

    if _is_login_provisioning_blocked_summary(summary):
        blocked_reason = str(summary.get("failure_reason") or summary.get("reason") or "orphan_challenge_provenance_weak")
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code="orphan_challenge_provenance_weak",
            error_message_safe=CLIENT_SAFE_ORPHAN_CHALLENGE_MESSAGE,
        )
        _reconcile_linked_run(
            account_id=account_id,
            run_id=run_id,
            terminal_status="failed",
            request_id=request_id,
            exit_code=exit_code,
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message="Login provisioning blocked pending canonical challenge recovery.",
            run_id=run_id,
            payload={
                "request_id": request_id,
                "exit_code": exit_code,
                "blocked_reason": blocked_reason,
                **({"login_provisioner_summary": summary} if summary else {}),
            },
        )
        return

    _safe_complete_account_run_request(
        request_id,
        cfg.worker_id,
        "failed",
        error_code="worker_exit_nonzero",
        error_message_safe=f"Worker subprocess exited with code {exit_code}.",
    )
    _reconcile_linked_run(
        account_id=account_id,
        run_id=run_id,
        terminal_status="failed",
        request_id=request_id,
        exit_code=exit_code,
    )
    _audit(
        account_id=account_id,
        action_type="manual_run_failed",
        status="failed",
        message=f"Manual run failed with exit code {exit_code}.",
        run_id=run_id,
        payload={
            "request_id": request_id,
            "exit_code": exit_code,
            **({"login_provisioner_summary": summary} if summary else {}),
        },
    )
    _publish_run_failure_incident(
        request_id=request_id,
        account_id=account_id,
        run_id=run_id,
        run_type=run_type,
        exit_code=exit_code,
        timed_out=False,
        canceled=canceled,
    )


def _terminate_subprocess(proc: subprocess.Popen[Any]) -> int:
    proc.send_signal(signal.SIGTERM)
    try:
        return int(proc.wait(timeout=30))
    except subprocess.TimeoutExpired:
        proc.kill()
        return int(proc.wait(timeout=10))


def _wait_for_subprocess(
    cfg: DispatcherConfig,
    proc: subprocess.Popen[Any],
    *,
    request_id: str,
    account_id: str,
    device_id: str | None = None,
    device_lock_renewal: bool = False,
) -> tuple[int, bool]:
    deadline = time.monotonic() + cfg.subprocess_timeout_seconds
    next_lock_renew = time.monotonic()
    lock_renew_interval = max(30.0, float(cfg.heartbeat_seconds) * 2.0)
    while True:
        exit_code = proc.poll()
        if exit_code is not None:
            return int(exit_code), False

        latest = get_account_run_request(request_id)
        if latest and (latest.get("status") == "canceled" or latest.get("cancel_requested_at")):
            log(
                "info",
                "manual_run_subprocess_cancel_requested",
                account_id=account_id,
                request_id=request_id,
                worker_id=cfg.worker_id,
            )
            return _terminate_subprocess(proc), False

        if device_lock_renewal and device_id and time.monotonic() >= next_lock_renew:
            renew_device_lock(
                device_id=device_id,
                worker_id=cfg.worker_id,
                request_id=request_id,
            )
            next_lock_renew = time.monotonic() + lock_renew_interval

        if time.monotonic() >= deadline:
            return _terminate_subprocess(proc), True

        time.sleep(1.0)


def _handle_claimed_request(cfg: DispatcherConfig, request: dict[str, Any]) -> None:
    request_id = normalize_request_uuid(request.get("id"))
    account_id = normalize_request_uuid(request.get("account_id"))
    run_type = str(request.get("requested_run_type") or "").strip().lower()
    if not request_id or not account_id or not run_type:
        log(
            "warning",
            "run_control_skip_claimed_request_invalid_row",
            has_request_id=bool(request_id),
            has_account_id=bool(account_id),
            has_run_type=bool(run_type),
            worker_id=cfg.worker_id,
        )
        return

    allowed, block_reason = _account_is_launch_allowed(account_id, cfg)
    if not allowed:
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code=block_reason or "blocked",
            error_message_safe=f"Run request blocked: {block_reason or 'blocked'}.",
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message=f"Run request blocked: {block_reason or 'blocked'}.",
            payload={"request_id": request_id, "reason": block_reason},
        )
        return

    starting = mark_account_run_request_starting(request_id, cfg.worker_id)
    if not starting:
        return

    ok_assignment, assignment_reason, dispatch_ctx = _validate_assignment(account_id, run_type, cfg)
    safe_dispatch = sensitive_log_fields(dispatch_ctx)
    if not ok_assignment:
        error_code = assignment_reason or "assignment_blocked"
        if (
            _is_scheduled_session_preflight_run_type(run_type)
            and error_code == "assignment_device_missing_adb_serial"
        ):
            from scheduled_session_preflight_control import terminalize_scheduled_session_preflight_request

            terminalize_scheduled_session_preflight_request(
                request=request,
                worker_id=cfg.worker_id,
                preflight_status="preflight_blocked",
                reason_code="device_serial_missing",
            )
            error_code = "device_serial_missing"
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code=error_code,
            error_message_safe=f"Assignment blocked: {error_code}.",
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message=f"Assignment blocked: {error_code}.",
            payload={"request_id": request_id, "assignment": safe_dispatch},
        )
        return

    request_metadata = dict(request.get("metadata_safe") or {})
    policy_ok, policy_reason, policy_ctx = evaluate_queued_run_commercial_policy(
        account_id,
        request_metadata,
        request_created_at=str(request.get("created_at") or "").strip() or None,
    )
    if not policy_ok:
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code=policy_reason or "commercial_policy_revision_changed",
            error_message_safe="Run request blocked: commercial package changed since queue.",
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message="Run request blocked: commercial package changed since queue.",
            payload={"request_id": request_id, "policy": policy_ctx},
        )
        return

    if _is_scheduled_session_preflight_run_type(run_type):
        from scheduled_session_preflight_control import (
            evaluate_scheduled_session_preflight_claim,
            terminalize_scheduled_session_preflight_request,
        )

        ok_preflight, terminal_status, terminal_reason = evaluate_scheduled_session_preflight_claim(request=request)
        if not ok_preflight:
            terminalize_scheduled_session_preflight_request(
                request=request,
                worker_id=cfg.worker_id,
                preflight_status=str(terminal_status or "preflight_invalidated"),
                reason_code=str(terminal_reason or "preflight_unavailable"),
            )
            _audit(
                account_id=account_id,
                action_type="manual_run_blocked",
                status="blocked",
                message=f"Scheduled session preflight terminalized: {terminal_reason or terminal_status}.",
                payload={"request_id": request_id, "run_type": run_type, "reason": terminal_reason},
            )
            return

    adb_serial = str(dispatch_ctx.get("adb_serial") or "").strip()
    log(
        "info",
        "run_dispatcher_device_serial_resolved",
        **safe_dispatch,
        adb_serial_present=bool(adb_serial),
    )

    _audit(
        account_id=account_id,
        action_type="manual_run_claimed",
        status="success",
        message="Manual run request claimed by dispatcher.",
        payload={
            "request_id": request_id,
            "worker_id": cfg.worker_id,
            "assignment": safe_dispatch,
            "adb_serial_present": bool(adb_serial),
            "commercial_policy": policy_ctx,
        },
    )

    auto_restart_policy: dict[str, Any] | None = None
    device_id = str(dispatch_ctx.get("device_id") or "").strip()
    device_lock_active = False
    lock_owner_worker_id = cfg.worker_id
    if is_auto_restart_request(request_metadata):
        ok_resume, resume_reason, auto_restart_policy = validate_auto_restart_request_at_claim(
            account_id=account_id,
            metadata=request_metadata,
        )
        log_auto_restart_claim_validation(
            account_id=account_id,
            request_id=request_id,
            ok=ok_resume,
            reason=resume_reason,
        )
        if not ok_resume:
            if is_hard_stop_reason(resume_reason):
                from auto_restart_hard_stop import execute_auto_restart_hard_stop

                execute_auto_restart_hard_stop(
                    account_id=account_id,
                    reason=resume_reason,
                    request_id=request_id,
                    device_id=device_id or None,
                    app_instance_id=str(dispatch_ctx.get("app_instance_id") or "").strip() or None,
                    execution_worker_id=str(request_metadata.get("execution_worker_id") or "").strip() or None,
                    trigger_source=str(request_metadata.get("trigger_source") or "").strip() or None,
                    worker_id=cfg.worker_id,
                    evidence={"metadata_redacted": True, "claim_validation": resume_reason, "execution_worker_id": cfg.worker_id, "worker_id": cfg.worker_id},
                    cancel_pending=False,
                )
            _safe_complete_account_run_request(
                request_id,
                cfg.worker_id,
                "blocked",
                error_code=resume_reason or "resume_runtime_not_supported",
                error_message_safe=f"Auto Restart blocked: {resume_reason or 'resume_runtime_not_supported'}.",
            )
            if device_id:
                tick_worker = str(request_metadata.get("worker_id") or "").strip() or cfg.worker_id
                release_device_lock(device_id=device_id, worker_id=tick_worker, request_id=request_id)
            else:
                tick_worker = str(request_metadata.get("worker_id") or "").strip() or cfg.worker_id
                release_device_lock_for_request(request_id=request_id, worker_id=tick_worker)
            return
        if device_id:
            transfer_device_lock(
                device_id=device_id,
                request_id=request_id,
                new_worker_id=cfg.worker_id,
            )
            renew_device_lock(device_id=device_id, worker_id=cfg.worker_id, request_id=request_id)
            device_lock_active = True
            lock_owner_worker_id = cfg.worker_id
    elif device_id and _is_device_bound_run_type(run_type):
        transfer_result = transfer_device_lock(
            device_id=device_id,
            request_id=request_id,
            new_worker_id=cfg.worker_id,
        )
        if not bool((transfer_result or {}).get("transferred")):
            pending_worker = _pending_manual_lock_worker_id(request_id)
            acquired = acquire_device_lock(
                device_id=device_id,
                worker_id=cfg.worker_id,
                account_id=account_id,
                app_instance_id=str(dispatch_ctx.get("app_instance_id") or "").strip() or None,
                reason="manual_run",
            )
            if not bool((acquired or {}).get("acquired")):
                _safe_complete_account_run_request(
                    request_id,
                    cfg.worker_id,
                    "blocked",
                    error_code="device_lock_held",
                    error_message_safe="Manual run blocked: assigned phone is reserved by another active session.",
                )
                _audit(
                    account_id=account_id,
                    action_type="manual_run_blocked",
                    status="blocked",
                    message="Manual run blocked because the assigned phone is reserved.",
                    payload={"request_id": request_id, "run_type": run_type, "device_id": device_id},
                )
                release_device_lock(device_id=device_id, worker_id=pending_worker, request_id=request_id)
                return
            transfer_device_lock(
                device_id=device_id,
                request_id=request_id,
                new_worker_id=cfg.worker_id,
            )
        renew_device_lock(device_id=device_id, worker_id=cfg.worker_id, request_id=request_id)
        device_lock_active = True
        lock_owner_worker_id = cfg.worker_id

    if (_is_login_run_type(run_type) or _is_orphan_recovery_run_type(run_type)) and not adb_serial:
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code="login_device_serial_required",
            error_message_safe="Login run blocked: assigned device serial is required.",
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message="Login run blocked: assigned device serial is required.",
            payload={"request_id": request_id, "run_type": run_type},
        )
        return

    if _is_scheduled_session_preflight_run_type(run_type) and not adb_serial:
        from scheduled_session_preflight_control import terminalize_scheduled_session_preflight_request

        terminalize_scheduled_session_preflight_request(
            request=request,
            worker_id=cfg.worker_id,
            preflight_status="preflight_blocked",
            reason_code="device_serial_missing",
        )
        _safe_complete_account_run_request(
            request_id,
            cfg.worker_id,
            "blocked",
            error_code="device_serial_missing",
            error_message_safe="Scheduled session preflight blocked: device serial missing.",
        )
        _audit(
            account_id=account_id,
            action_type="manual_run_blocked",
            status="blocked",
            message="Scheduled session preflight blocked: device serial missing.",
            payload={"request_id": request_id, "run_type": run_type, "assignment": safe_dispatch},
        )
        if device_lock_active and device_id:
            release_device_lock(device_id=device_id, worker_id=lock_owner_worker_id, request_id=request_id)
        return

    if run_type == "login_email_code_resume":
        action_id = str(
            request_metadata.get("action_id") or request_metadata.get("verification_action_id") or ""
        ).strip()
        if action_id:
            try:
                from login_challenge_runtime import mark_verification_action_resume_running

                mark_verification_action_resume_running(
                    action_id=action_id,
                    account_id=account_id,
                    run_request_id=request_id,
                )
            except Exception as exc:
                log(
                    "warning",
                    "login_resume_action_state_update_failed",
                    account_id=account_id,
                    request_id=request_id,
                    error=str(exc)[:200],
                )

    linked_login_run_id = None
    if _is_login_run_type(run_type):
        linked_login_run_id = _create_and_link_login_run(account_id, request_id, cfg.worker_id)

    cmd = _build_runner_command(
        account_id,
        run_type,
        linked_login_run_id or request_id,
        device_serial=adb_serial,
        package_name=dispatch_ctx.get("package_name") or dispatch_ctx.get("app_package") or dispatch_ctx.get("package"),
        app_instance_id=dispatch_ctx.get("app_instance_id"),
        metadata_safe={
            **request_metadata,
            "assignment_id": dispatch_ctx.get("assignment_id"),
            "assignment_updated_at": dispatch_ctx.get("assignment_updated_at"),
            "credentials_version": dispatch_ctx.get("credentials_version"),
        },
    )
    log(
        "info",
        "manual_run_subprocess_starting",
        account_id=account_id,
        request_id=request_id,
        run_type=run_type,
        worker_id=cfg.worker_id,
        device_id=dispatch_ctx.get("device_id"),
        app_instance_id=dispatch_ctx.get("app_instance_id"),
        adb_serial_present=bool(adb_serial),
    )
    _heartbeat(cfg, status="running", metadata={"active_request_id": request_id, "account_id": account_id})

    subprocess_env = (
        _login_provisioner_env()
        if _is_login_run_type(run_type) or _is_orphan_recovery_run_type(run_type)
        else runner_subprocess_env()
    )
    if auto_restart_policy:
        subprocess_env = {**subprocess_env, **runner_env_for_resume_policy(auto_restart_policy)}

    global _last_integration_noop_proof
    _last_integration_noop_proof = None
    if _integration_noop_runner_enabled():
        subprocess_env = {
            **os.environ,
            **subprocess_env,
            "RUN_CONTROL_INTEGRATION_MODE": "1",
            "RUN_CONTROL_INTEGRATION_NOOP_RUNNER": "1",
            "RUN_CONTROL_INTEGRATION_NO_ADB": "1",
        }
        completed = subprocess.run(
            cmd,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=subprocess_env,
            capture_output=True,
            text=True,
        )
        for line in (completed.stdout or "").splitlines():
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                parsed = json.loads(stripped)
                if parsed.get("runner") == "auto_restart_integration_noop":
                    _last_integration_noop_proof = parsed
                    break
            except json.JSONDecodeError:
                continue
        exit_code = int(completed.returncode)
        timed_out = False
    else:
        proc = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=subprocess_env,
        )
        exit_code, timed_out = _wait_for_subprocess(
            cfg,
            proc,
            request_id=request_id,
            account_id=account_id,
            device_id=device_id if device_lock_active else None,
            device_lock_renewal=device_lock_active,
        )

    if device_id and device_lock_active:
        if _is_scheduled_session_preflight_run_type(run_type) and int(exit_code) == 0:
            renew_device_lock(
                device_id=device_id,
                worker_id=lock_owner_worker_id,
                request_id=request_id,
            )
        else:
            release_device_lock(device_id=device_id, worker_id=lock_owner_worker_id, request_id=request_id)

    _finalize_manual_run_after_subprocess(
        cfg,
        request_id=request_id,
        account_id=account_id,
        exit_code=int(exit_code),
        timed_out=timed_out,
        request_snapshot=request,
    )


def _claim_next_dispatch_request(
    cfg: DispatcherConfig,
    *,
    heartbeat_status: str = "idle",
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not cfg.enabled:
        return None, {"ok": False, "reason": "disabled"}

    _heartbeat(cfg, status=heartbeat_status)
    reclaimed = reclaim_stale_account_run_requests(cfg.worker_id)
    try:
        from scheduled_session_preflight_control import reconcile_stale_scheduled_session_preflight_requests

        reconciled = reconcile_stale_scheduled_session_preflight_requests(
            worker_id=cfg.worker_id,
            allowed_run_types=cfg.allowed_run_types,
        )
        if reconciled:
            log(
                "info",
                "scheduled_session_preflight_stale_reconciled",
                worker_id=cfg.worker_id,
                reconciled_count=reconciled,
            )
    except Exception as exc:
        log(
            "warning",
            "scheduled_session_preflight_reconcile_failed",
            worker_id=cfg.worker_id,
            error=str(exc)[:200],
        )

    if cfg.health_only or not cfg.launch_enabled:
        return None, {"ok": True, "mode": "health_only", "reclaimed": reclaimed}

    request = claim_next_account_run_request(
        cfg.worker_id,
        lease_seconds=cfg.lease_seconds,
        allowed_run_types=cfg.allowed_run_types,
    )
    if not request:
        return None, {"ok": True, "mode": "idle", "reclaimed": reclaimed}

    request_id = normalize_request_uuid(request.get("id"))
    if not request_id:
        log(
            "warning",
            "run_control_skip_invalid_claim_row",
            worker_id=cfg.worker_id,
        )
        return None, {
            "ok": True,
            "mode": "idle",
            "reason": "invalid_claim_row",
            "reclaimed": reclaimed,
        }

    account_id = normalize_request_uuid(request.get("account_id"))
    result: dict[str, Any] = {
        "ok": True,
        "mode": "processed",
        "request_id": request_id,
        "account_id": account_id,
        "reclaimed": reclaimed,
    }
    if _last_integration_noop_proof:
        result["noop_proof"] = _last_integration_noop_proof
    return request, result


def run_once(cfg: DispatcherConfig | None = None) -> dict[str, Any]:
    _assert_integration_safety()
    cfg = cfg or load_dispatcher_config()
    request, result = _claim_next_dispatch_request(cfg)
    if request is not None:
        _handle_claimed_request(cfg, request)
    return result


def evaluate_launch_mode_startup_preflight_with_retries(cfg: DispatcherConfig) -> dict[str, Any]:
    max_attempts = max(1, _env_int("RUN_CONTROL_DISPATCHER_PREFLIGHT_RETRIES", 3))
    backoff_s = max(0.0, _env_float("RUN_CONTROL_DISPATCHER_PREFLIGHT_RETRY_SECONDS", 2.0))
    last: dict[str, Any] = {"ok": False, "reason": "active_queue_read_failed"}
    for attempt in range(1, max_attempts + 1):
        last = evaluate_launch_mode_startup_preflight(cfg)
        if last.get("ok"):
            return last
        reason = str(last.get("reason") or "")
        if reason == "active_queue_present":
            return last
        if not reason.startswith("supabase_"):
            return last
        if attempt < max_attempts:
            log(
                "warning",
                "run_control_dispatcher_preflight_retry",
                worker_id=cfg.worker_id,
                attempt=attempt,
                max_attempts=max_attempts,
                reason=reason,
                error=str(last.get("error") or "")[:200],
            )
            if backoff_s > 0:
                time.sleep(backoff_s * attempt)
    return last


def _collect_completed_dispatch_tasks(
    active_tasks: set[Future[None]],
) -> None:
    completed_tasks = {task for task in active_tasks if task.done()}
    active_tasks.difference_update(completed_tasks)
    for task in completed_tasks:
        task.result()


def _submit_dispatch_task_if_capacity(
    executor: ThreadPoolExecutor,
    active_tasks: set[Future[None]],
    cfg: DispatcherConfig,
    request: dict[str, Any],
) -> bool:
    if len(active_tasks) >= cfg.max_concurrent_subprocesses:
        return False
    active_tasks.add(executor.submit(_handle_claimed_request, cfg, request))
    return True


def run_forever(cfg: DispatcherConfig | None = None) -> int:
    cfg = cfg or load_dispatcher_config()
    if not cfg.enabled:
        log("error", "run_control_dispatcher_disabled")
        return 2

    preflight = evaluate_launch_mode_startup_preflight_with_retries(cfg)
    if not preflight.get("ok"):
        reason = str(preflight.get("reason") or "blocked")
        log(
            "error",
            "run_control_dispatcher_launch_preflight_blocked",
            worker_id=cfg.worker_id,
            reason=reason,
            active_count=int(preflight.get("active_count") or 0),
            allow_existing_queue=bool(preflight.get("allow_existing_queue")),
            active_requests=list(preflight.get("requests") or [])[:5],
            error=str(preflight.get("error") or "")[:200],
        )
        if reason.startswith("supabase_"):
            try:
                _heartbeat(
                    cfg,
                    status=f"unhealthy:{reason}",
                    metadata={"preflight_blocked": True, "supabase_reason": reason},
                )
            except Exception as heartbeat_exc:
                log(
                    "warning",
                    "run_control_dispatcher_unhealthy_heartbeat_failed",
                    worker_id=cfg.worker_id,
                    reason=reason,
                    error=str(heartbeat_exc)[:200],
                )
        return 3

    log(
        "info",
        "run_control_dispatcher_started",
        worker_id=cfg.worker_id,
        health_only=cfg.health_only,
        launch_enabled=cfg.launch_enabled,
        allowed_run_types=cfg.allowed_run_types,
        startup_active_count=int(preflight.get("active_count") or 0),
        startup_allow_existing_queue=bool(preflight.get("allow_existing_queue")),
    )
    _heartbeat(cfg, status="starting")

    stop = False

    def _handle_signal(signum, frame) -> None:
        nonlocal stop
        stop = True
        log("info", "run_control_dispatcher_stop_signal", signal=signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    last_heartbeat = 0.0
    last_auto_restart_tick = 0.0
    last_loop_error_key = ""
    last_loop_error_logged_at = 0.0
    consecutive_loop_errors = 0
    active_tasks: set[Future[None]] = set()
    executor = ThreadPoolExecutor(
        max_workers=cfg.max_concurrent_subprocesses,
        thread_name_prefix="run-dispatch",
    )
    try:
        while not stop:
            try:
                _collect_completed_dispatch_tasks(active_tasks)

                now_loop = time.monotonic()
                if should_run_auto_restart_tick(
                    last_tick_monotonic=last_auto_restart_tick,
                    now_monotonic=now_loop,
                ):
                    last_auto_restart_tick = now_loop
                    tick_result = run_auto_restart_dispatcher_tick(
                        worker_id=cfg.worker_id,
                        dispatcher_reliable=consecutive_loop_errors == 0,
                    )
                    if tick_result.get("ok"):
                        log(
                            "info",
                            "auto_restart_dispatcher_tick_observed",
                            worker_id=cfg.worker_id,
                            skipped=bool(tick_result.get("skipped")),
                        )
                    elif tick_result.get("skipped"):
                        log(
                            "info",
                            "auto_restart_dispatcher_tick_skipped",
                            worker_id=cfg.worker_id,
                            reason=str(tick_result.get("reason") or "skipped"),
                        )
                if len(active_tasks) < cfg.max_concurrent_subprocesses:
                    request, _claim_result = _claim_next_dispatch_request(
                        cfg,
                        heartbeat_status="running" if active_tasks else "idle",
                    )
                    if request is not None:
                        _submit_dispatch_task_if_capacity(
                            executor,
                            active_tasks,
                            cfg,
                            request,
                        )
                last_loop_error_key = ""
                consecutive_loop_errors = 0
            except Exception as exc:
                consecutive_loop_errors += 1
                err = str(exc)[:500]
                now = time.monotonic()
                err_key = err[:200]
                if err_key != last_loop_error_key or (now - last_loop_error_logged_at) >= 60.0:
                    log(
                        "error",
                        "run_control_dispatcher_loop_failed",
                        error=err,
                        consecutive_loop_errors=consecutive_loop_errors,
                        max_consecutive_loop_errors=cfg.max_consecutive_loop_errors,
                    )
                    last_loop_error_key = err_key
                    last_loop_error_logged_at = now
                if consecutive_loop_errors >= cfg.max_consecutive_loop_errors:
                    log(
                        "error",
                        "run_control_dispatcher_exit_after_repeated_loop_errors",
                        worker_id=cfg.worker_id,
                        consecutive_loop_errors=consecutive_loop_errors,
                    )
                    return 4
            now = time.monotonic()
            if now - last_heartbeat >= cfg.heartbeat_seconds:
                _heartbeat(
                    cfg,
                    status="running" if active_tasks else "idle",
                    metadata={"active_subprocesses": len(active_tasks)},
                )
                last_heartbeat = now
            time.sleep(cfg.poll_seconds)
    finally:
        executor.shutdown(wait=True)

    _heartbeat(cfg, status="stopping")
    log("info", "run_control_dispatcher_stopped", worker_id=cfg.worker_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if args and args[0] in {"--preflight", "preflight"}:
        cfg = load_dispatcher_config()
        if "--json" in args:
            with contextlib.redirect_stdout(sys.stderr):
                result = evaluate_launch_mode_startup_preflight_with_retries(cfg)
            print(json.dumps(result, default=str, sort_keys=True))
        else:
            result = evaluate_launch_mode_startup_preflight_with_retries(cfg)
            print(result)
        return 0 if result.get("ok") else 3
    if args and args[0] in {"--once", "once"}:
        result = run_once()
        print(result)
        return 0 if result.get("ok") else 1
    return run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
